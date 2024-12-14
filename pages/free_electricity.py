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


def app():
    st.set_page_config(page_icon="🆓", page_title="Octopus Free Electricity calculator")
    st.header("🆓 Octopus Free Electricity calculator")

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

    results(api_key)


@st.cache_data(ttl="600s", show_spinner=False)
def results(api_key):
    debug = debug_message if "debug" in st.query_params else debug_noop
    bar = st.progress(0, text="Authenticating...")
    sessions = db.free_sessions()

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

        bar.progress(0.1, text="Getting meters...")
        agreements = api.agreements(account.number)
        if agreements:
            break
    else:
        error("No agreements on account")

    bar.progress(0.15, text="Getting tariffs...")
    import_mpan = None
    mpans: dict[str, ElectricityMeterPoint] = {}
    for agreement in agreements:
        debug(agreement)
        mpans[agreement.meterPoint.mpan] = agreement.meterPoint
        # Find import meter
        product = get_product(agreement.tariff.productCode)
        if product.direction == "IMPORT":
            import_mpan = agreement.meterPoint.mpan
            break
    debug(mpans)

    if not import_mpan:
        error("No import meter found.")
        raise Exception("unreachable code")

    import_readings = calculation.Readings(mpans[import_mpan])

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
            f"Getting readings for session #{i+1} ({ss.timestamp:%b %d})...",
            start,
            start + ticks_per_session,
        )
        debug(f"session: {ss}")
        calc = calculation.Calculation.free_session(ss, sessions)
        calc.calculate(api, import_readings, None, ticks, debug)
        calcs.append(calc)
        rows.append(calc.free_row())

        # Update in place
        with placeholder.container():
            st.subheader("Results")
            st.dataframe(
                rows,
                column_config={
                    "session": st.column_config.DatetimeColumn("Session"),
                    "import": st.column_config.NumberColumn("Imported", format="%.2f kWh"),
                    "export": st.column_config.NumberColumn("Exported", format="%.2f kWh"),
                    "baseline": st.column_config.NumberColumn("Baseline", format="%.2f kWh"),
                    "free": st.column_config.NumberColumn("Free", format="%.2f kWh"),
                },
                width=600,
            )

    bar.progress(1.0, text="Done")


app()
