from datetime import date, datetime

import numpy as np
import pendulum

from .api import API, ElectricityMeterPoint, SavingSession
from .db import FreeSession


def phh(hh: int):
    return pendulum.duration(minutes=hh * 30)


def bank_holiday(day):
    return str(day) in {
        "2025-04-18",
        "2025-04-21",
        "2025-05-05",
        "2025-05-26",
        "2025-08-25",
        "2025-12-25",
        "2025-12-26",
    }


def weekday(day):
    """True if day is a weekday (excluding bank holidays)"""
    return pendulum.MONDAY <= day.day_of_week <= pendulum.FRIDAY and not bank_holiday(day.date())


class Readings:
    """Cached table of readings"""

    def __init__(self, meter_point: ElectricityMeterPoint):
        self.meter_point = meter_point
        self.requested = set()
        self.hh = {}

    def get_readings(self, api: API, ts: datetime, hh: int, debug):
        half_hours = list(pendulum.interval(ts, ts + phh(hh - 1)).range("minutes", 30))
        if not self.requested.issuperset(half_hours):
            start_at = ts - phh(100 - hh)
            debug(f"Fetching {self.meter_point.mpan} readings from {start_at}")

            # Request readings and cache the lot
            readings = api.half_hourly_readings(
                mpan=self.meter_point.mpan,
                meter=self.meter_point.meters[0].id,
                start_at=start_at,
                first=100,
                before=None,
            )
            if readings:
                debug(f"Received {len(readings)} readings from {readings[0].startAt} to {readings[-1].endAt}")
                self.requested.update(pendulum.interval(start_at, readings[-1].startAt).range("minutes", 30))
            else:
                debug("Received no readings")
                self.requested.update(pendulum.interval(start_at, start_at + phh(99)).range("minutes", 30))

            for reading in readings:
                self.hh[reading.startAt] = reading.value

        try:
            values = [self.hh[t] for t in half_hours]
            return np.array(values)
        except KeyError:
            raise ValueError("missing readings") from None


class Calculation:
    def __init__(
        self,
        code: str | None,
        start: datetime,
        duration: int,
        rewardPerKwhInOctoPoints: int,
        previous_session_days: set[date],
    ) -> None:
        self.code = code
        self.start = start
        self.duration = duration
        self.rewardPerKwhInOctoPoints = rewardPerKwhInOctoPoints
        self.previous_session_days = previous_session_days
        self.is_saving_session = code is not None

        self.session_import = None
        self.session_export = None
        self.baseline_days = []
        self.baseline_import = None
        self.baseline_export = None
        self.baseline = None
        self.kwh = None
        self.points = None

    @staticmethod
    def saving_session(ss: SavingSession, sessions: list[SavingSession]):
        previous_session_days = {ss.startAt.date() for ss in sessions}
        return Calculation(ss.code, ss.startAt, ss.hh, ss.rewardPerKwhInOctoPoints, previous_session_days)

    @staticmethod
    def free_session(ss: FreeSession, sessions: list[FreeSession]):
        previous_session_days = {ss.timestamp.date() for ss in sessions}
        return Calculation(None, ss.timestamp, ss.duration, 0, previous_session_days)

    def calculate(
        self,
        api: API,
        import_readings: Readings,
        export_readings: Readings | None,
        tick,
        debug,
    ):
        # Baseline from meter readings from the same time as the Session over the past 10 weekdays (excluding any days
        # with a Saving Session), past 4 weekend days if Saving Session is on a weekend.
        days_required = 10 if self.is_weekday else 4
        previous = pendulum.interval(self.start.subtract(days=1), self.start.subtract(days=61))

        try:
            self.session_import = import_readings.get_readings(api, self.start, self.duration, debug)
            debug(f"session import: {self.session_import}")
        except ValueError:
            # incomplete, but useful to still calculate baseline
            debug("session incomplete")
        next(tick)

        if export_readings:
            try:
                self.session_export = export_readings.get_readings(api, self.start, self.duration, debug)
                debug(f"session export: {self.session_export}")
            except ValueError:
                debug("missing export readings")
        next(tick)

        days = 0
        baseline_import = []
        baseline_export = []
        for dt in previous.range("days"):
            if weekday(dt) != weekday(self.start):
                continue
            if dt.date() in self.previous_session_days:
                continue

            try:
                import_values = import_readings.get_readings(api, dt, self.duration, debug)
                baseline_import.append(import_values)
                debug(f"baseline day #{days}: {dt} import: {import_values}")
            except ValueError:
                debug(f"skipped day: {dt} missing readings")
                continue
            next(tick)

            if export_readings:
                try:
                    export_values = export_readings.get_readings(api, dt, self.duration, debug)
                    baseline_export.append(export_values)
                    debug(f"baseline day #{days}: {dt} export: {export_values}")
                except ValueError:
                    debug(f"baseline day: {dt} missing export readings")
            next(tick)

            self.baseline_days.append(dt)
            days += 1
            if days == days_required:
                break

        if baseline_import:
            self.baseline_import = np.asarray(baseline_import)
            self.baseline = self.avg_baseline_import

            if baseline_export:
                self.baseline_export = np.asarray(baseline_export)
                self.baseline = self.baseline - self.avg_baseline_export

            if self.session_import is not None:
                session = self.session_import
                if self.session_export is not None:
                    session = session - self.session_export

                if self.is_saving_session:
                    # saving is calculated per settlement period (half hour), and only positive savings considered
                    self.kwh = (self.baseline - session).clip(min=0)
                    self.points = np.round(self.kwh * self.rewardPerKwhInOctoPoints / 8).astype(int) * 8
                else:
                    self.kwh = (session - self.baseline).clip(min=0)
                    self.points = 0

    def free_row(self):
        ret = {
            "session": self.start.in_timezone("Europe/London").format("YYYY/MM/DD HH:mm"),
        }
        if self.session_import is not None:
            ret["import"] = self.session_import.sum()
        if self.baseline is not None:
            ret["baseline"] = self.baseline.sum()
        if self.kwh is not None:
            ret["free"] = self.kwh.sum()
        return ret

    def saving_session_row(self):
        ret = {
            "session": self.start.in_timezone("Europe/London").format("YYYY/MM/DD HH:mm"),
        }
        if self.session_import is not None:
            ret["import"] = self.session_import.sum()
        if self.session_export is not None:
            ret["export"] = self.session_export.sum()
        if self.baseline is not None:
            ret["baseline"] = self.baseline.sum()
        if self.kwh is not None:
            ret["saved"] = self.kwh.sum()
            reward = int(self.points.sum())
            ret["reward"] = reward
            ret["earnings"] = reward / 800
        return ret

    @property
    def is_weekday(self) -> bool:
        return weekday(self.start)

    @property
    def avg_baseline_import(self):
        if self.baseline_import is None:
            raise ValueError("baseline_import missing")
        if self.is_weekday:
            return self.baseline_import.mean(axis=0)
        # weekend: the mean average of the 2 median days will be taken
        return np.median(self.baseline_import, axis=0)

    @property
    def avg_baseline_export(self):
        if self.baseline_export is None:
            raise ValueError("baseline_export missing")
        if self.is_weekday:
            return self.baseline_export.mean(axis=0)
        # weekend: the mean average of the 2 median days will be taken
        return np.median(self.baseline_export, axis=0)

    def dbrow(self, id_lookup: dict):
        ret = {
            "saving_session_id": id_lookup[self.code],
        }
        if self.session_import is not None:
            ret["session_import"] = self.session_import.sum()
        if self.session_export is not None:
            ret["session_export"] = self.session_export.sum()
        if self.baseline_import is not None:
            ret["baseline_import"] = self.avg_baseline_import.sum()
        if self.baseline_export is not None:
            ret["baseline_export"] = self.avg_baseline_export.sum()
        if self.points is not None:
            ret["points"] = int(self.points.sum())
        return ret
