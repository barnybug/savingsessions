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


def main():
    pg = st.navigation([
        st.Page("pages/ss.py", title="Saving Sessions", icon="🐙"),
        st.Page("pages/league.py", title="League", icon="🏆"),
        st.Page("pages/free_electricity.py", title="Free Electricity", icon="🆓"),
    ])
    pg.run()


if __name__ == "__main__":
    main()
