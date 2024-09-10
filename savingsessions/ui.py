import streamlit as st

from savingsessions.api import API, AuthenticationError


def error(msg: str):
    st.error(msg, icon="⚠️")
    st.stop()


def debug_message(msg):
    st.write(msg)


def debug_noop(msg):
    pass


@st.cache_data(ttl=None)  # never expire
def get_product(code: str):
    api = API()  # unauthenticated
    return api.energy_product(code)


def get_account_number(api_key):
    api = API()
    try:
        api.authenticate(api_key)
    except AuthenticationError:
        error("Authentication error, check your API key")

    accounts = api.accounts()
    if not accounts:
        error("No accounts found")
    account = accounts[0]
    return account.number
