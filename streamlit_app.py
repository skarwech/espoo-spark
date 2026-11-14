"""Streamlit entry point for the Spark web application.

Locally this starts the existing FastAPI app in a managed background thread and displays its pages in Streamlit.
For hosted deployments, set SPARK_STREAMLIT_API_URL to a publicly reachable FastAPI base URL.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

import streamlit as st


LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = int(os.environ.get("SPARK_STREAMLIT_API_PORT", "8778"))
LOCAL_URL = f"http://{LOCAL_HOST}:{LOCAL_PORT}"


def _probe_spark(base_url: str) -> bool:
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/health", timeout=1.0) as response:
            payload = json.loads(response.read())
        return payload.get("service") == "spark" and payload.get("status") == "ok"
    except (OSError, ValueError, URLError):
        return False


@st.cache_resource(show_spinner="Starting Spark…")
def _start_api():
    configured_url = os.environ.get("SPARK_STREAMLIT_API_URL", "").strip().rstrip("/")
    if configured_url:
        if not _probe_spark(configured_url):
            raise RuntimeError("SPARK_STREAMLIT_API_URL did not respond as a Spark API. Check the URL and backend health.")
        return configured_url, None

    if _probe_spark(LOCAL_URL):
        return LOCAL_URL, None

    with socket.socket() as probe:
        try:
            probe.bind((LOCAL_HOST, LOCAL_PORT))
        except OSError as error:
            raise RuntimeError(
                f"Port {LOCAL_PORT} is already in use by another service. Stop it or set SPARK_STREAMLIT_API_PORT."
            ) from error

    os.environ.setdefault("SPARK_ENGINE", "d1")
    os.environ.setdefault("SPARK_AMENDMENT", "")
    import uvicorn

    from api.main import app

    server = uvicorn.Server(uvicorn.Config(app, host=LOCAL_HOST, port=LOCAL_PORT, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, name="spark-api", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and thread.is_alive():
        if server.started and _probe_spark(LOCAL_URL):
            return LOCAL_URL, (server, thread)
        time.sleep(0.1)
    raise RuntimeError("Spark API did not become healthy within 15 seconds. Check the terminal for its startup error.")


st.set_page_config(page_title="Spark", page_icon="🌱", layout="wide")
st.title("Spark")
st.caption("Work evidence and event-planning prototype · synthetic data · human decisions only")

pages = {
    "Home": "/",
    "Need Studio": "/need-studio",
    "Skill Story": "/skill-story",
    "Blind View": "/blind-view",
    "Event Console": "/event-console",
    "Release Report": "/reports/f16.html",
}

with st.sidebar:
    st.header("Navigate")
    selected = st.radio("Spark page", list(pages), label_visibility="collapsed")
    st.divider()
    st.caption("The Streamlit shell hosts the existing Spark pages. Use the page navigation inside the frame to move between workflows.")

try:
    api_url, _server = _start_api()
except Exception as error:
    st.error(str(error))
    st.info("Local run: start with `make serve-streamlit`. Hosted run: configure `SPARK_STREAMLIT_API_URL` to a reachable Spark API.")
    st.stop()

if not os.environ.get("SPARK_STREAMLIT_API_URL"):
    st.sidebar.caption(f"Local API ready · {api_url}")

st.iframe(f"{api_url}{pages[selected]}", height=900, width="stretch", tab_index=0)
