"""Native Streamlit screens backed by Spark's existing Python workflow modules."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, time
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent


@st.cache_data(show_spinner=False)
def _read_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _registered_needs() -> list[dict]:
    return _read_json("data/out/needs.json")


def _need_label(need_id: str) -> str:
    need = next((row for row in _registered_needs() if row["need_id"] == need_id), None)
    if not need:
        return need_id
    companies = {row["company_id"]: row["name"] for row in _read_json("data/out/companies.json")}
    return f"{need_id} · {companies.get(need['company_id'], 'Company')}"


def render(page: str) -> None:
    routes = {
        "Home": _home,
        "Need Studio": _need_studio,
        "Skill Story": _skill_story,
        "Blind View": _blind_view,
        "Event Console": _event_console,
        "Event Pass": _event_pass,
        "Release Report": _release_report,
    }
    routes[page]()


def _home() -> None:
    st.header("Needs first. People decide.")
    st.write("Companies describe the work they need done. Jobseekers describe work they have done. Spark shows the evidence behind discussion suggestions; it does not score or rank people.")

    needs = _registered_needs()
    people = _read_json("data/out/jobseekers.json")
    pairs = _read_json("data/engine_d1/lens.json")
    first, second, third = st.columns(3)
    first.metric("Synthetic needs", len(needs))
    second.metric("Synthetic jobseekers", len(people))
    third.metric("Evidence comparisons", len(pairs))

    st.subheader("Explore a saved Match Lens")
    need_ids = sorted(row["need_id"] for row in needs)
    selected_need = st.selectbox("Need", need_ids, format_func=_need_label, key="home_need")
    candidate_ids = sorted({row["candidate_id"] for row in pairs if row["need_id"] == selected_need})
    candidate_id = st.selectbox("Anonymous jobseeker ID", candidate_ids, key="home_candidate")
    pair = next(row for row in pairs if row["need_id"] == selected_need and row["candidate_id"] == candidate_id)
    st.markdown(f"**Discussion tier:** {pair['tier_label']}")
    st.write(pair["why"])
    st.caption("Tier labels are discussion aids. They are not a score or a hiring decision.")
    for item in pair["grid"]:
        with st.container(border=True):
            st.markdown(f"**{item['priority'].replace('_', ' ').title()} outcome** · {item['support'].title()}")
            st.write(item["outcome"])
            if item.get("quote"):
                st.markdown(f"> {item['quote']}")
            st.caption(item["reason"])


def _need_studio() -> None:
    from api import need_studio, store
    from engine import llm

    st.header("Need Studio")
    st.write("Describe the outcomes you need. The assistant turns your answers into a draft Need Card; review and confirm it before use.")
    st.info(need_studio.DISCLOSURE)
    session_key = "spark_need_session"
    session_id = st.session_state.get(session_key)

    if st.session_state.get("spark_need_reset"):
        st.session_state.pop(session_key, None)
        st.session_state.pop("spark_need_reset", None)
        session_id = None

    if not session_id:
        with st.form("need_studio_start"):
            company = st.text_input("Company name", max_chars=120)
            industry = st.text_input("Industry", max_chars=120)
            start = st.form_submit_button("Start interview", type="primary")
        if start:
            view = need_studio.start(company, industry)
            st.session_state[session_key] = view["session_id"]
            st.rerun()
        st.caption("A configured Verda Kimi API key is required for new interview answers. Set `VERDA_INFERENCE_API_KEY_KIMI` in Streamlit secrets.")
        return

    state = store.get_interview(session_id)
    if state is None:
        st.warning("This interview session is no longer available. Start a new interview.")
        if st.button("Start over"):
            st.session_state.pop(session_key, None)
            st.rerun()
        return

    for message in state["transcript"]:
        role = "assistant" if message["role"] == "assistant" else "user"
        with st.chat_message(role):
            st.write(message["text"])

    if state["done"]:
        record = state["record"]
        card = record["card"]
        st.subheader(f"Draft Need Card · {card['need_id']} v{card['version']}")
        st.write(record["summary"])
        for warning in record["warnings"]:
            st.warning(warning)
        with st.expander("Review structured card"):
            st.json(card)
        current = store.get_need(card["need_id"], card["version"])
        if current and current["card"]["confirmed"]:
            st.success("This Need Card has been confirmed.")
        elif st.button("Confirm Need Card", type="primary"):
            need_studio.confirm(card["need_id"], card["version"])
            st.success("Need Card confirmed. It can now be used in downstream workflows.")
            st.rerun()
    else:
        with st.form(f"need_answer_{session_id}"):
            answer = st.text_area("Your answer", max_chars=4000, height=140)
            send = st.form_submit_button("Send answer", type="primary")
        if send:
            if not answer.strip():
                st.warning("Enter an answer to continue.")
            else:
                try:
                    need_studio.answer(session_id, answer)
                    st.rerun()
                except llm.LLMError as error:
                    st.error(str(error))
                except Exception as error:
                    st.error(f"The interview could not continue: {error}")

    if st.button("Start a new interview"):
        st.session_state["spark_need_reset"] = True
        st.rerun()


def _skill_story() -> None:
    from api import compliance, skill_story

    st.header("Skill Story")
    st.write("Describe work you did, in your own words. Spark stores what you submit; there is no score or ranking.")
    st.info("Leave out your name, contact details, birth date, nationality and school names. Your story is stored only after you agree; you can delete it with the code shown after submission.")

    with st.form("skill_story_form"):
        lang = st.selectbox("Story language", ("en", "fi", "sv", "ar", "other"))
        problem = st.text_area("1. A problem you solved", max_chars=6000, height=110)
        did = st.text_area("2. What you did personally", max_chars=6000, height=130)
        differently = st.text_area("3. What you would do differently now", max_chars=6000, height=110)
        next_step = st.text_area("4. What you want next and your constraints", max_chars=6000, height=110)

        with st.expander("Practical details and optional context"):
            work_mode = st.selectbox("Work mode", ("hybrid", "onsite", "remote"))
            start = st.text_input("Earliest start (YYYY-MM)", value="2027-01", max_chars=7)
            levels = ("Not stated", "A1", "A2", "B1", "B2", "C1", "C2")
            langs = []
            cols = st.columns(3)
            for col, code, name in zip(cols, ("en", "fi", "sv"), ("English", "Finnish", "Swedish")):
                level = col.selectbox(f"{name} level", levels, key=f"story_lang_{code}")
                if level != "Not stated":
                    langs.append({"lang": code, "level": level})
            relocation = st.checkbox("I need relocation support")
            ai_used = st.checkbox("I used AI to help write this (not used for matching)")
            mini_case = st.text_area("Optional mini case answer", max_chars=4000)
            private_context = st.text_area("Optional private context (not used for matching)", max_chars=2000)

        consent = st.checkbox("I agree that Spark stores this story to suggest event conversations.")
        check_requested = st.form_submit_button("Check for personal details")
        submit_requested = st.form_submit_button("Submit my Skill Story", type="primary")

    payload = {
        "lang": lang if lang != "other" else "en",
        "problem": problem,
        "did": did,
        "differently": differently,
        "next": next_step,
        "ai_used": ai_used,
        "constraints": {"work_mode": work_mode, "start": start,
                        "languages": langs, "needs_relocation": relocation},
        "mini_case": mini_case,
        "private_context": private_context,
        "consent": consent,
    }

    if check_requested:
        warnings = skill_story.check({"problem": problem, "did": did, "differently": differently, "next": next_step})
        if any(warnings.values()):
            st.warning("Review these possible personal details before submitting.")
            st.json(warnings)
        else:
            st.success("No likely personal details were detected. You are still responsible for what you choose to share.")

    if submit_requested:
        try:
            result = skill_story.submit(payload)
            st.session_state["spark_story_receipt"] = result["story_id"]
        except ValueError as error:
            st.error(str(error))

    story_id = st.session_state.get("spark_story_receipt")
    if story_id:
        st.success("Skill Story submitted.")
        st.code(story_id, language=None)
        st.caption("Keep this one-time code private. It is required to delete this story.")
        with st.expander("Delete this Skill Story"):
            delete_code = st.text_input("Enter the one-time code", key="story_delete_code")
            if st.button("Delete submitted story", type="secondary"):
                try:
                    compliance.delete_participant(story_id, delete_code)
                    st.session_state.pop("spark_story_receipt", None)
                    st.success("Story deleted.")
                    st.rerun()
                except (PermissionError, KeyError) as error:
                    st.error(str(error))


def _blind_view() -> None:
    from api import blind, event_ops

    st.header("Blind View")
    st.write("Review redacted work evidence. Cards are discussion aids, not rankings. Contact details require both people to opt in through separate sessions.")
    needs = _registered_needs()
    need_ids = sorted(row["need_id"] for row in needs)
    selected = st.selectbox("Choose a saved synthetic Need Card", need_ids, format_func=_need_label, key="blind_need")
    if st.button("Open blind evidence cards", type="primary"):
        card = next(row for row in needs if row["need_id"] == selected)
        try:
            cards = blind.blind_cards(card)
            st.session_state["spark_blind_cards"] = sorted(cards, key=lambda item: item["match_id"])
            st.session_state["spark_blind_need"] = selected
        except Exception as error:
            st.error(f"Blind cards could not be loaded: {error}")

    cards = st.session_state.get("spark_blind_cards", [])
    if cards and st.session_state.get("spark_blind_need") == selected:
        st.caption(f"{len(cards)} synthetic jobseekers · shown in opaque-ID order, with no ranking")
        for index, card in enumerate(cards, start=1):
            with st.expander(f"Discussion card {index} · {card['tier_label']}"):
                st.write(card["why"])
                for item in card["coverage"]:
                    st.markdown(f"**{item['priority'].title()} outcome · {item['evidence'].title()}**")
                    st.write(item["outcome"])
                    if item["excerpt"]:
                        st.markdown(f"> {item['excerpt']}")
                    st.caption(item["reason"])
                st.caption(f"Practical preferences: start {card['practical_preferences']['start']} · {card['practical_preferences']['work_mode']}")
                if st.button("Show optional conversation prompts", key=f"blind_prompt_{card['match_id']}"):
                    try:
                        st.session_state[f"blind_prompts_{card['match_id']}"] = event_ops.conversation_card(card["match_id"])
                    except Exception as error:
                        st.error(f"Prompts could not be prepared: {error}")
                prompts = st.session_state.get(f"blind_prompts_{card['match_id']}")
                if prompts:
                    for question in prompts["questions"]:
                        st.write(f"• {question['question']}")


def _event_console() -> None:
    from api import compliance, event_ops, store

    st.header("Event Console")
    st.write("Draft and approve a synthetic meeting agenda, issue private event passes, and review event evidence.")
    expected = os.environ.get("SPARK_ORGANIZER_TOKEN", "")
    if not expected:
        st.warning("Organizer actions are disabled until `SPARK_ORGANIZER_TOKEN` is configured in Streamlit secrets.")
    token = st.text_input("Organizer token", type="password", key="spark_organizer_token")
    allowed = compliance.organizer_allowed(token)
    event_id = st.text_input("Event ID", value="espoo-2027", max_chars=48, key="spark_event_id")
    col1, col2 = st.columns(2)
    with col1:
        event_date = st.date_input("Event date", value=date(2027, 3, 15))
    with col2:
        start_time = st.time_input("Start time", value=time(9, 0), step=900)

    if st.button("Build draft agenda", type="primary", disabled=not allowed):
        try:
            agenda = event_ops.create_agenda(event_id, event_date.isoformat(), start_time.strftime("%H:%M"))
            compliance.log("agenda_drafted", token, event_id, slots=len(agenda["slots"]))
            st.success(f"Drafted {len(agenda['slots'])} conversation slots.")
        except Exception as error:
            st.error(f"Agenda could not be drafted: {error}")

    record = store.get_agenda(event_id) if event_id else None
    if not record:
        return
    agenda = record["agenda"]
    st.subheader("Agenda review")
    st.caption(f"{len(agenda['slots'])} slots · {agenda['date']} · {agenda['why']}")
    st.dataframe([
        {key: slot[key] for key in ("slot_id", "time", "company", "industry", "need_id", "tier")}
        for slot in agenda["slots"]
    ], width="stretch", hide_index=True)

    if record["approved"]:
        st.success("Agenda approved.")
    elif st.button("Approve agenda", disabled=not allowed):
        try:
            compliance.approve_event(event_id, token)
            st.success("Agenda approved.")
            st.rerun()
        except Exception as error:
            st.error(f"Agenda approval failed: {error}")

    if record["approved"] and not store.event_pass_count(event_id):
        if st.button("Issue private passes", disabled=not allowed):
            try:
                issued = compliance.issue_passes(event_id, token)
                st.session_state[f"spark_passes_{event_id}"] = issued
                st.success(f"Issued {len(issued)} private passes. Deliver each code separately to its attendee.")
            except Exception as error:
                st.error(f"Passes could not be issued: {error}")

    for item in st.session_state.get(f"spark_passes_{event_id}", []):
        with st.expander(f"Private pass · {item['attendee_ref']}"):
            st.code(item["token"], language=None)
            st.caption("This is a bearer access code. Deliver it privately; the app does not send messages.")

    tools = st.columns(3)
    with tools[0]:
        if st.button("Load dashboard", disabled=not allowed or not record["approved"]):
            try:
                st.session_state["spark_event_dashboard"] = event_ops.dashboard(event_id)
            except Exception as error:
                st.error(str(error))
    with tools[1]:
        if st.button("Run bias audit", disabled=not allowed):
            try:
                report = event_ops.fairness_audit()
                compliance.log("bias_audit_viewed", token, "synthetic-pool", identity_twins=report["identity_twins"]["n"])
                st.session_state["spark_event_audit"] = report
            except Exception as error:
                st.error(f"Audit could not be loaded: {error}")
    with tools[2]:
        if st.button("Compliance snapshot", disabled=not allowed):
            st.session_state["spark_event_compliance"] = compliance.review_snapshot()

    for key, label in (("spark_event_dashboard", "Dashboard"), ("spark_event_audit", "Bias audit"), ("spark_event_compliance", "Compliance snapshot")):
        value = st.session_state.get(key)
        if value:
            with st.expander(label, expanded=True):
                st.json(value)

    slot_ids = [slot["slot_id"] for slot in agenda["slots"]]
    if slot_ids:
        slot_id = st.selectbox("Evidence trace for slot", slot_ids)
        if st.button("Show evidence trace", disabled=not allowed):
            try:
                st.session_state["spark_event_trace"] = compliance.trace_slot(event_id, slot_id)
            except Exception as error:
                st.error(f"Trace unavailable: {error}")
        if st.session_state.get("spark_event_trace"):
            st.json(st.session_state["spark_event_trace"])


def _event_pass() -> None:
    from api import event_ops

    st.header("Event Pass")
    st.write("Enter the private access code provided by the event organizer. Your agenda is verified against the approved event record.")
    token = st.text_input("Private pass code", type="password", key="spark_pass_token")
    if st.button("Open my pass", type="primary"):
        try:
            st.session_state["spark_open_pass"] = event_ops.open_pass(token)
            st.session_state["spark_open_pass_token"] = token
        except Exception as error:
            st.error(f"Pass unavailable: {error}")
    payload = st.session_state.get("spark_open_pass")
    if not payload:
        return
    st.subheader(f"Your agenda · {payload['date']}")
    st.write(payload["venue"])
    for meeting in payload["agenda"]:
        with st.container(border=True):
            st.markdown(f"**{meeting['time']} · {meeting['company']}**")
            st.write(meeting["why"])
            for question in meeting["questions"]:
                st.write(f"• {question['question']}")
            if meeting["conflicts"]:
                st.caption("Practical points to discuss: " + "; ".join(item.get("message", item.get("kind", "")) for item in meeting["conflicts"]))
    slots = {meeting["slot_id"]: f"{meeting['time']} · {meeting['company']}" for meeting in payload["agenda"]}
    if slots:
        with st.form("spark_pass_pulse"):
            slot_id = st.selectbox("Which conversation is this feedback about?", list(slots), format_func=lambda value: slots[value])
            response = st.radio("Was it useful?", ("useful", "not_useful"), format_func=lambda value: "Yes" if value == "useful" else "Not yet")
            submit = st.form_submit_button("Send feedback")
        if submit:
            try:
                result = event_ops.add_pulse(st.session_state["spark_open_pass_token"], uuid.uuid4().hex, slot_id, response)
                st.success("Feedback recorded." if not result["duplicate"] else "Feedback was already recorded.")
            except Exception as error:
                st.error(f"Feedback could not be recorded: {error}")


def _release_report() -> None:
    st.header("F9–F16 Release Report")
    st.caption("Unmeasured human and device checks remain pending in this prototype.")
    report = ROOT / "reports" / "f16.html"
    if report.exists():
        st.html(report.read_text(encoding="utf-8"))
    else:
        st.warning("Release report has not been built yet. Run `make report` in the project checkout.")
