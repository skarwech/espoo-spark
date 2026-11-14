"""Spark synthetic-data prototype web app (F1–F16). Run with SPARK_ENGINE=d1 via ``make serve-app``."""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api import blind, compliance, event_ops, need_studio, pool_preview, skill_story, store
from engine import llm, run

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="Spark", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
app.mount("/reports", StaticFiles(directory=ROOT / "reports"), name="reports")
pages = Jinja2Templates(directory=ROOT / "web" / "templates")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "spark"}


def _seekers() -> dict:
    """Constraints of the registered pool (start, languages, work mode); used only for the stated-constraint groups."""
    return {j["candidate_id"]: j["constraints"] for j in json.loads((ROOT / "data" / "out" / "jobseekers.json").read_text(encoding="utf-8"))}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return pages.TemplateResponse(request, "index.html", {"engine": llm.ENGINE_MODEL})


@app.get("/need-studio", response_class=HTMLResponse)
def need_studio_page(request: Request):
    return pages.TemplateResponse(request, "need_studio.html", {"disclosure": need_studio.DISCLOSURE})


@app.get("/skill-story", response_class=HTMLResponse)
def skill_story_page(request: Request):
    return pages.TemplateResponse(request, "skill_story.html", {"max_words": skill_story.MAX_WORDS, "levels": skill_story.LEVELS})


@app.get("/blind-view", response_class=HTMLResponse)
def blind_view_page(request: Request):
    return pages.TemplateResponse(request, "blind_view.html", {})


@app.get("/event-console", response_class=HTMLResponse)
def event_console_page(request: Request):
    return pages.TemplateResponse(request, "event_console.html", {})


@app.get("/event-pass/{token}", response_class=HTMLResponse)
def event_pass_page(request: Request, token: str):
    if len(token) > 100:
        raise HTTPException(404, "unknown pass")
    return pages.TemplateResponse(request, "event_pass.html", {"token": token})


# ---------- F1 Need Studio ----------
@app.post("/api/interviews")
def interview_start(body: dict = Body(default={})):
    return need_studio.start(str(body.get("company", ""))[:120], str(body.get("industry", ""))[:120])


@app.post("/api/interviews/{session_id}/answer")
def interview_answer(session_id: str, body: dict = Body(...)):
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise HTTPException(422, "answer text is required (at most 4000 characters)")
    try:
        return need_studio.answer(session_id, text)
    except KeyError:
        raise HTTPException(404, "unknown interview")
    except llm.LLMError as e:
        raise HTTPException(503, f"The assistant is unavailable right now ({e}). Your answers are saved; please try again.")


@app.get("/api/needs/{need_id}")
def need_get(need_id: str):
    rec = store.get_need(need_id)
    if rec is None:
        raise HTTPException(404, "unknown need")
    return {k: rec[k] for k in ("card", "unmapped_skills", "warnings", "summary")}


@app.post("/api/needs/{need_id}/confirm")
def need_confirm(need_id: str, body: dict = Body(...)):
    try:
        rec = need_studio.confirm(need_id, int(body.get("version", 0)))
    except (KeyError, ValueError):
        raise HTTPException(404, "unknown need version")
    return {"need_id": need_id, "version": rec["card"]["version"], "confirmed": True}


# ---------- F2 Pool Preview (approval gate: confirmed cards only) ----------
@app.get("/api/needs/{need_id}/preview")
def need_preview(need_id: str):
    from engine import live
    try:
        rec = need_studio.usable(need_id)
    except KeyError:
        raise HTTPException(404, "unknown need")
    except PermissionError as e:
        raise HTTPException(409, f"{e}: confirm the Need Card before it is used")
    card = rec["card"]
    try:
        tiers = live.tiers_for(card)
    except llm.LLMError as e:
        raise HTTPException(503, f"The matching engine is unavailable right now ({e}).")
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return pool_preview.preview(card, tiers, _seekers(), as_of, llm.ENGINE_MODEL, lambda n: live.tiers_for(n))


# ---------- F3 Skill Story ----------
@app.post("/api/stories/check")
def story_check(body: dict = Body(...)):
    return skill_story.check({b: str(body.get(b, "")) for b in skill_story.BOXES})


@app.post("/api/stories/clarify")
def story_clarify(body: dict = Body(...)):
    try:
        out = skill_story.clarify(str(body.get("box", "")), str(body.get("text", "")), str(body.get("lang", "en")))
    except llm.LLMError as e:
        raise HTTPException(503, f"Clarifying questions are unavailable right now ({e}). You can submit without them.")
    return {"questions": out["questions"]}


@app.post("/api/stories")
def story_submit(body: dict = Body(...)):
    try:
        return skill_story.submit(body)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/stories/{story_id}")
def story_delete(story_id: str, x_delete_code: str = Header(default="")):
    try:
        return compliance.delete_participant(story_id, x_delete_code)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except KeyError:
        raise HTTPException(404, "unknown story")


@app.get("/api/stories/{story_id}/recruiter-view")
def story_recruiter_view(story_id: str):
    v = skill_story.recruiter_view(story_id)
    if v is None:
        raise HTTPException(404, "unknown story")
    return v


# ---------- F8 Blind View and Reveal ----------
@app.get("/api/needs/{need_id}/blind-candidates")
def blind_candidates(need_id: str):
    try:
        card = need_studio.usable(need_id)["card"]
        cards = blind.blind_cards(card)
    except KeyError:
        raise HTTPException(404, "unknown need")
    except PermissionError as e:
        raise HTTPException(409, f"{e}: confirm the Need Card before opening blind candidate cards")
    except (RuntimeError, llm.LLMError) as e:
        raise HTTPException(503, f"Blind cards are unavailable right now ({e}).")
    return {"need_id": card["need_id"], "version": card["version"], "framing": "Blind evidence cards are discussion aids, not rankings. "
            "Identity and contact details stay hidden until both people opt in.", "candidates": cards}


@app.post("/api/blind-matches/{match_id}/opt-in/{role}")
def reveal_opt_in(match_id: str, role: str):
    try:
        store.give_reveal_consent(match_id, role)
        return blind.reveal(match_id, role)
    except KeyError:
        raise HTTPException(404, "unknown blind match")
    except ValueError:
        raise HTTPException(422, "role must be recruiter or candidate")


@app.get("/api/blind-matches/{match_id}/reveal")
def reveal_status(match_id: str, role: str):
    try:
        return blind.reveal(match_id, role)
    except KeyError:
        raise HTTPException(404, "unknown blind match")
    except ValueError:
        raise HTTPException(422, "role must be recruiter or candidate")


# ---------- F9 Conversation Cards ----------
@app.get("/api/blind-matches/{match_id}/conversation-card")
def conversation_card(match_id: str):
    try:
        return event_ops.conversation_card(match_id)
    except KeyError:
        raise HTTPException(404, "unknown blind match")
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------- F10 Agenda, F11 Event Pass, F12 Pulse ----------
def _organizer(token: str | None) -> str:
    if not compliance.organizer_allowed(token):
        raise HTTPException(403, "organiser access required")
    return token


@app.post("/api/events/{event_id}/agenda")
def event_agenda(event_id: str, body: dict = Body(...), x_organizer_token: str | None = Header(default=None)):
    actor = _organizer(x_organizer_token)
    try:
        agenda = event_ops.create_agenda(event_id, str(body.get("date", "")), str(body.get("start_time", "09:00")),
                                         int(body.get("interval_minutes", 15)), int(body.get("slots_per_company", 15)),
                                         str(body.get("seed", "spark-event-1")))
        compliance.log("agenda_drafted", actor, event_id, slots=len(agenda["slots"]))
        return {"approved": False, **agenda}
    except (TypeError, ValueError, KeyError) as e:
        raise HTTPException(422, str(e))


@app.get("/api/events/{event_id}/agenda")
def event_agenda_get(event_id: str, x_organizer_token: str | None = Header(default=None)):
    _organizer(x_organizer_token)
    record = store.get_agenda(event_id)
    if record is None:
        raise HTTPException(404, "unknown event")
    return {"approved": record["approved"], "approved_at": record["approved_at"],
            "passes_issued": store.event_pass_count(event_id) > 0, **record["agenda"]}


@app.post("/api/events/{event_id}/approve")
def event_agenda_approve(event_id: str, x_organizer_token: str | None = Header(default=None)):
    actor = _organizer(x_organizer_token)
    try:
        return compliance.approve_event(event_id, actor)
    except KeyError:
        raise HTTPException(404, "unknown event")


@app.post("/api/events/{event_id}/passes")
def event_passes(event_id: str, x_organizer_token: str | None = Header(default=None)):
    actor = _organizer(x_organizer_token)
    try:
        return {"passes": compliance.issue_passes(event_id, actor)}
    except KeyError:
        raise HTTPException(404, "unknown event")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/passes/{token}")
def pass_get(token: str):
    try:
        return event_ops.open_pass(token)
    except KeyError:
        raise HTTPException(404, "unknown pass")
    except TimeoutError:
        raise HTTPException(410, "Expired")
    except ValueError as e:
        raise HTTPException(410, str(e))


@app.post("/api/passes/{token}/pulse")
def pass_pulse(token: str, body: dict = Body(...)):
    try:
        return event_ops.add_pulse(token, str(body.get("client_event_id", "")), str(body.get("slot_id", "")),
                                   str(body.get("response", "")))
    except KeyError:
        raise HTTPException(410, "unknown or expired pass")
    except TimeoutError:
        raise HTTPException(410, "Expired")
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------- F13 Organiser Dashboard, F14 Bias Audit, F15 Oversight ----------
@app.get("/api/organizer/events/{event_id}/dashboard")
def organizer_dashboard(event_id: str, x_organizer_token: str | None = Header(default=None)):
    _organizer(x_organizer_token)
    try:
        return event_ops.dashboard(event_id)
    except KeyError:
        raise HTTPException(404, "unknown event")
    except PermissionError as e:
        raise HTTPException(409, str(e))


@app.get("/api/organizer/events/{event_id}/trace/{slot_id}")
def organizer_trace(event_id: str, slot_id: str, x_organizer_token: str | None = Header(default=None)):
    _organizer(x_organizer_token)
    try:
        return compliance.trace_slot(event_id, slot_id)
    except KeyError:
        raise HTTPException(404, "unknown event slot")


@app.get("/api/organizer/bias-audit")
def organizer_bias_audit(x_organizer_token: str | None = Header(default=None)):
    actor = _organizer(x_organizer_token)
    report = event_ops.fairness_audit()
    compliance.log("bias_audit_viewed", actor, "synthetic-pool", identity_twins=report["identity_twins"]["n"])
    return report


@app.get("/api/organizer/compliance")
def organizer_compliance(x_organizer_token: str | None = Header(default=None)):
    _organizer(x_organizer_token)
    return compliance.review_snapshot()
