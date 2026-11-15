"""Streamlit entry point for local and hosted Spark demos.

All screens render in Streamlit itself. This is important on hosted services: a browser cannot reach a
loopback API running inside the Streamlit container.
"""
from __future__ import annotations

import os

import streamlit as st


def _load_secrets() -> None:
    """Expose configured Streamlit secrets to the existing project modules as environment variables."""
    names = (
        "SPARK_ENGINE",
        "SPARK_ORGANIZER_TOKEN",
        "SPARK_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "VERDA_INFERENCE_API_KEY",
        "VERDA_INFERENCE_API_KEY_KIMI",
        "VERDA_INFERENCE_API_KEY_DEEPSEEK",
        "SPARK_LLM_BUDGET_USD",
        "SPARK_VERDA_BUDGET_USD",
        "SPARK_DB",
    )
    try:
        for name in names:
            value = st.secrets.get(name)
            if value and not os.environ.get(name):
                os.environ[name] = str(value)
    except Exception:
        # Local runs do not need a secrets.toml file.
        pass
    os.environ.setdefault("SPARK_ENGINE", "d1")
    os.environ.setdefault("SPARK_AMENDMENT", "")


_load_secrets()
st.set_page_config(page_title="Spark", page_icon="🌱", layout="wide")

from streamlit_ui import render  # noqa: E402


def _reset_demo_session() -> None:
    """Clear this browser session's UI state without deleting persisted app records."""
    prefixes = ("spark_", "blind_", "home_", "story_")
    for key in list(st.session_state):
        if key != "spark_nav" and key.startswith(prefixes):
            del st.session_state[key]
    st.session_state["spark_nav"] = "Home"


st.title("Spark")
st.caption("Work evidence and event-planning prototype · synthetic data · human decisions only")

pages = ("Home", "Need Studio", "Skill Story", "Blind View", "Event Console", "Event Pass", "Release Report")
with st.sidebar:
    st.header("Navigate")
    selected = st.radio("Spark page", pages, label_visibility="collapsed", key="spark_nav")
    st.divider()
    st.caption("This demo uses synthetic data. People make every hiring decision.")
    st.button("Reset demo session", on_click=_reset_demo_session)
    st.caption("Resets this browser's workflow state. Saved stories and event records remain.")

render(selected)
