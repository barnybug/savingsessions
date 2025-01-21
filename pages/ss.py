import hashlib

import numpy as np
import pendulum
import streamlit as st

from savingsessions import calculation, db
from savingsessions.api import (
    API,
    AuthenticationError,
    ElectricityMeterPoint,
)
from savingsessions.ui import debug_message, debug_noop, error, get_account_number, get_product


SESSION_START = pendulum.datetime(2024, 12, 1)


def app():
    st.set_page_config(page_icon="🐙", page_title="Octopus Saving Sessions calculator")
    st.header("🐙 Octopus Saving Sessions calculator")

    st.subheader("Your Octopus API Key")
    st.markdown("Find this in your online dashboard: https://octopus.energy/dashboard/developer/")
    if "api_key" not in st.session_state and (api_key := st.query_params.get("api_key")):
        st.session_state["api_key"] = api_key
    api_key = st.text_input("API key:", key="api_key", placeholder="sk_live_...")
    if not api_key:
        st.info(
            "This app never stores your API key. If you have any concerns you can check out the [source code]"
            "(https://github.com/barnybug/savingsessions) for the app, and please by all means 'Regenerate' your key at"
            " the link above after using this."
        )
        st.stop()

    if st.query_params.get("api_key") != api_key:
        st.query_params["api_key"] = api_key

    st.info("Tip: bookmark this url to return with your API key remembered.", icon="🔖")

    calcs = results(api_key)
    complete = [calc for calc in calcs if calc.points is not None]

    if complete:
        st.subheader("Enter the league!")
        st.write(
            "For a bit of fun you can add your results to our league table. This will enter you for the above results"
            " for all complete sessions."
        )
        name = st.text_input(
            "Name or alias", max_chars=80, placeholder="Please enter your name or alias 😎", key="name_input"
        )
        name = name.strip()
        if st.button("Submit") and name:
            with st.spinner("Entering..."):
                account_no = get_account_number(api_key)
                sessions = db.saving_sessions()
                id_lookup = {s["code"]: s["id"] for s in sessions}
                # store the hash of account for privacy
                account_hash = hashlib.sha256(account_no.encode("utf-8")).hexdigest()
                common = {
                    "account": account_hash,
                    "username": name,
                }
                rows = [calc.dbrow(id_lookup) | common for calc in complete]
                db.upsert_results(rows)

            st.write("🎉 Entered! Go check out your placement in the [league tables](/league)")
            db.results.clear()  # expire cache


@st.cache_data(ttl="600s", show_spinner=False)
def results(api_key):
    debug = debug_message if "debug" in st.query_params else debug_noop
    bar = st.progress(0, text="Authenticating...")

    api = API()
    try:
        api.authenticate(api_key)
    except AuthenticationError:
        error("Authentication error, check your API key")

    bar.progress(0.05, text="Getting account...")
    accounts = api.accounts()
    if not accounts:
        error("No accounts found")

    for account in accounts:
        debug(account)

        bar.progress(0.07, text="Getting meters...")
        agreements = api.agreements(account.number)
        if agreements:
            break
    else:
        error("No agreements on account")

    bar.progress(0.1, text="Getting sessions...")
    res = api.saving_sessions(account.number)
    debug(res)
    if not res.hasJoinedCampaign:
        error("Sorry, it looks like you've not joined saving sessions.")
    if not res.signedUpMeterPoint:
        error("Sorry, it looks like you haven't a meter point for saving sessions.")
    now = pendulum.now()
    all_sessions = res.sessions
    # Filter to this season
    sessions = [session for session in res.sessions if session.startAt > SESSION_START]
    sessions = [session for session in sessions if session.id in res.joinedEvents or session.startAt > now]
    if not sessions:
        error("Not joined any saving sessions yet this season.")

    bar.progress(0.15, text="Getting tariffs...")
    import_mpan = None
    export_mpan = None
    mpans: dict[str, ElectricityMeterPoint] = {}
    for agreement in agreements:
        debug(agreement)
        mpans[agreement.meterPoint.mpan] = agreement.meterPoint
        if agreement.meterPoint.mpan == res.signedUpMeterPoint:
            import_mpan = res.signedUpMeterPoint
            continue
        # Find export meter
        product = get_product(agreement.tariff.productCode)
        if product.direction == "EXPORT":
            export_mpan = agreement.meterPoint.mpan
        elif not import_mpan:
            import_mpan = agreement.meterPoint.mpan
    debug(mpans)

    if not import_mpan:
        error("No import meter found.")
        raise Exception("unreachable code")

    import_readings = calculation.Readings(mpans[import_mpan])
    if export_mpan:
        export_readings = calculation.Readings(mpans[export_mpan])
    else:
        st.info("Import meter only", icon="ℹ️")
        export_readings = None

    calcs = []
    rows = []
    total_ticks = 22

    def tick(message, start, end):
        for i in range(total_ticks):
            bar.progress(start + (end - start) * i / total_ticks, text=message)
            yield
        while True:
            yield

    placeholder = st.empty()

    ticks_per_session = 0.8 / len(sessions)
    for i, ss in enumerate(sessions):
        start = 0.2 + i * ticks_per_session
        ticks = tick(
            f"Getting readings for session #{i+1} ({ss.startAt:%b %d})...",
            start,
            start + ticks_per_session,
        )
        debug(f"session: {ss}")
        calc = calculation.Calculation.saving_session(ss, all_sessions)
        calc.calculate(api, import_readings, export_readings, ticks, debug)
        calcs.append(calc)
        rows.append(calc.saving_session_row())

        # Update in place
        with placeholder.container():
            st.subheader("Results")
            st.dataframe(
                rows,
                column_config={
                    "session": st.column_config.DatetimeColumn("Session", format="YYYY/MM/DD HH:mm"),
                    "import": st.column_config.NumberColumn("Imported", format="%.2f kWh"),
                    "export": st.column_config.NumberColumn("Exported", format="%.2f kWh"),
                    "baseline": st.column_config.NumberColumn("Baseline", format="%.2f kWh"),
                    "saved": st.column_config.NumberColumn("Saved", format="%.2f kWh"),
                    "reward": st.column_config.NumberColumn("Reward"),
                    "earnings": st.column_config.NumberColumn("Earnings", format="£%.2f"),
                },
            )

        # Session breakdown
        with st.expander(f"Session {ss.startAt:%b %d %Y} breakdown"):
            timestamps = [
                ts.strftime("%H:%M")
                for ts in pendulum.interval(ss.startAt, ss.endAt - pendulum.duration(minutes=30)).range("minutes", 30)
            ]
            days = [f"{day:%b %d}" for day in calc.baseline_days]

            if calc.baseline_import is not None:
                data = np.r_[
                    [timestamps],
                    calc.baseline_import,
                ].T
                st.dataframe(
                    data,
                    column_config={str(i): s for i, s in enumerate(["Baseline import"] + days)},
                )

            if calc.baseline_export is not None:
                data = np.r_[
                    [timestamps],
                    calc.baseline_export,
                ].T
                st.dataframe(
                    data,
                    column_config={str(i): s for i, s in enumerate(["Baseline export"] + days)},
                )

            data = {"Time": timestamps}
            if calc.baseline_import is not None:
                data["Baseline import"] = calc.avg_baseline_import.round(3)
            if calc.baseline_export is not None:
                data["Baseline export"] = calc.avg_baseline_export.round(3)
            if calc.session_import is not None:
                data["Session import"] = calc.session_import
            if calc.session_export is not None:
                data["Session export"] = calc.session_export
            if calc.kwh is not None:
                data["Net (kWh)"] = calc.kwh.round(3)
                data["Points"] = calc.points

            st.dataframe(
                np.asarray(list(data.values())).T,
                column_config={str(i): header for i, header in enumerate(data.keys())},
            )

    bar.progress(1.0, text="Done")

    for row in rows:
        if "reward" in row:
            continue
        ts = row["session"]
        st.info(f"Session on {ts} is awaiting readings...", icon="⌛")

    return calcs


app()
