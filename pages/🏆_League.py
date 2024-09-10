import pendulum
import streamlit as st

from savingsessions import db


def app():
    st.set_page_config(page_icon="🏆", page_title="Saving Sessions unofficial league")
    st.header("🏆 Saving Sessions unofficial league")
    code_to_id = {row["code"]: row["id"] for row in db.saving_sessions()}
    sessions = {row["code"]: row for row in db.saving_sessions()}

    def format_code(code):
        session = sessions[code]
        timestamp = pendulum.parse(session["timestamp"])
        points = session["points"]
        return f"{timestamp:%A %d %b %Y at %H:%M} ({points} points)"

    if "session" not in st.session_state and (param := st.query_params.get("session")):
        st.session_state["session"] = param

    code = st.selectbox(
        "Select session",
        list(sessions),
        format_func=format_code,
        key="session",
    )
    if not code:
        return

    if st.query_params.get("session") != code:
        st.query_params["session"] = code

    results = db.results(code_to_id[code])
    if not results:
        st.write("No entrants yet!")
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rows = [
        {"position": medals.get(pos, str(pos))} | result | {"earnings": result["points"] / 800}
        for pos, result in enumerate(results, 1)
    ]
    st.dataframe(
        rows,
        height=800,
        column_config={
            "position": "Position",
            "username": "Username",
            "baseline_import": st.column_config.NumberColumn("Baseline import", format="%.2f kWh"),
            "baseline_export": st.column_config.NumberColumn("Baseline export", format="%.2f kWh"),
            "session_import": st.column_config.NumberColumn("Session import", format="%.2f kWh"),
            "session_export": st.column_config.NumberColumn("Session export", format="%.2f kWh"),
            "points": st.column_config.NumberColumn("Points"),
            "earnings": st.column_config.NumberColumn("Earnings", format="£%.2f"),
        },
    )


app()
