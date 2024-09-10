import datetime
from dataclasses import dataclass

import pendulum
import streamlit as st
import supabase
from postgrest.exceptions import APIError


@st.cache_resource
def session():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return supabase.create_client(url, key)


@st.cache_data(ttl=600, show_spinner=False)
def saving_sessions():
    response = session().table("saving_sessions").select("*").order("timestamp", desc=True).execute()
    return response.data


@dataclass
class FreeSession:
    id: int
    timestamp: datetime.datetime
    duration: int


@st.cache_data(ttl=600, show_spinner=False)
def free_sessions():
    response = session().table("free_sessions").select("*").order("timestamp", desc=True).execute()
    rows = [
        FreeSession(
            id=row["id"],
            timestamp=pendulum.parse(row["timestamp"]),
            duration=row["duration"],
        )
        for row in response.data
    ]
    return rows


@st.cache_data(ttl=600, show_spinner=False)
def results(ss_id):
    response = (
        session()
        .table("results")
        .select("username,baseline_import,baseline_export,session_import,session_export,points")
        .eq("saving_session_id", ss_id)
        .order("points", desc=True)
        .execute()
    )
    return response.data


def upsert_results(rows):
    for row in rows:
        try:
            session().table("results").insert(row).execute()
        except APIError:
            session().table("results").update(row).eq("account", row["account"]).eq(
                "saving_session_id", row["saving_session_id"]
            ).execute()
