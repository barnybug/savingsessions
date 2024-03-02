from datetime import datetime

import numpy as np
import pendulum

from .api import API, ElectricityMeterPoint, SavingSession


def phh(hh: int):
    return pendulum.duration(minutes=hh * 30)


def weekday(day):
    """True if day is a weekday"""
    return pendulum.MONDAY <= day.day_of_week <= pendulum.FRIDAY


class Readings:
    """Cached table of readings"""

    def __init__(self, meter_point: ElectricityMeterPoint):
        self.meter_point = meter_point
        self.requested = set()
        self.hh = {}

    def get_readings(self, api: API, ts: datetime, hh: int, debug):
        half_hours = list(pendulum.period(ts, ts + phh(hh - 1)).range("minutes", 30))
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
                self.requested.update(pendulum.period(start_at, readings[-1].startAt).range("minutes", 30))
            else:
                debug("Received no readings")
                self.requested.update(pendulum.period(start_at, start_at + phh(99)).range("minutes", 30))

            for reading in readings:
                self.hh[reading.startAt] = reading.value

        try:
            values = [self.hh[t] for t in half_hours]
            return np.array(values)
        except KeyError:
            raise ValueError("missing readings") from None


class Calculation:
    def __init__(self, ss: SavingSession) -> None:
        self.ss = ss
        self.session_import = None
        self.session_export = None
        self.baseline_days = []
        self.baseline_import = None
        self.baseline_export = None
        self.baseline = None
        self.kwh = None
        self.points = None

    def calculate(
        self,
        api: API,
        sessions: list[SavingSession],
        import_readings: Readings,
        export_readings: Readings | None,
        tick,
        debug,
    ):
        # Baseline from meter readings from the same time as the Session over the past 10 weekdays (excluding any days
        # with a Saving Session), past 4 weekend days if Saving Session is on a weekend.
        days_required = 10 if self.is_weekday else 4
        previous_session_days = {ss.startAt.date() for ss in sessions}
        previous = pendulum.period(self.ss.startAt.subtract(days=1), self.ss.startAt.subtract(days=61))

        try:
            self.session_import = import_readings.get_readings(api, self.ss.startAt, self.ss.hh, debug)
            debug(f"session import: {self.session_import}")
        except ValueError:
            # incomplete, but useful to still calculate baseline
            debug("session incomplete")
        next(tick)

        if export_readings:
            try:
                self.session_export = export_readings.get_readings(api, self.ss.startAt, self.ss.hh, debug)
                debug(f"session export: {self.session_export}")
            except ValueError:
                debug("missing export readings")
        next(tick)

        days = 0
        baseline_import = []
        baseline_export = []
        for dt in previous.range("days"):
            if weekday(dt) != weekday(self.ss.startAt):
                continue
            if dt.date() in previous_session_days:
                continue

            try:
                import_values = import_readings.get_readings(api, dt, self.ss.hh, debug)
                baseline_import.append(import_values)
                debug(f"baseline day #{days}: {dt} import: {import_values}")
            except ValueError:
                debug(f"skipped day: {dt} missing readings")
                continue
            next(tick)

            if export_readings:
                try:
                    export_values = export_readings.get_readings(api, dt, self.ss.hh, debug)
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
                # saving is calculated per settlement period (half hour), and only positive savings considered
                self.kwh = (self.baseline - session).clip(min=0)
                self.points = np.round(self.kwh * self.ss.rewardPerKwhInOctoPoints / 8).astype(int) * 8

    def row(self):
        ret = {
            "session": self.ss.startAt,
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
        return weekday(self.ss.startAt)

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
            "saving_session_id": id_lookup[self.ss.code],
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
