// Need Studio (F1) and Pool Preview (F2). The server decides the flow; this page only shows it.
const $ = (id) => document.getElementById(id);
let session = null, need = null;

async function api(path, body, method = "POST") {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

function render(view) {
  const log = $("log");
  log.replaceChildren(...view.transcript.filter((t) => t.stage !== "disclosure").map((t) => {
    const li = document.createElement("li");
    li.className = t.role; li.dir = "auto"; li.textContent = t.text;
    return li;
  }));
  log.scrollTop = log.scrollHeight;
  if (view.done && view.record) showReview(view.record);
}

function showReview(rec) {
  need = rec.card;
  $("answer-form").hidden = true;
  $("review").hidden = false;
  $("card-summary").textContent = rec.summary;
  $("warnings").replaceChildren(...(rec.warnings.length ? rec.warnings : ["No warnings."]).map((w) => {
    const li = document.createElement("li"); li.textContent = w; if (!rec.warnings.length) li.className = "ok"; return li;
  }));
  $("card-json").textContent = JSON.stringify({ ...rec.card, unmapped_skills: rec.unmapped_skills }, null, 2);
  $("confirm").focus();
}

$("start").addEventListener("click", async () => {
  $("start").disabled = true;
  try {
    const v = await api("/api/interviews", { company: $("company").value, industry: $("industry").value });
    session = v.session_id;
    $("setup").hidden = true; $("chat").hidden = false;
    render(v); $("answer").focus();
  } catch (e) { alert(e.message); $("start").disabled = false; }
});

async function send(ev) {
  ev?.preventDefault();
  const text = $("answer").value.trim();
  if (!text || !session) return;
  $("send").disabled = true; $("status").textContent = "Thinking…";
  try {
    const v = await api(`/api/interviews/${session}/answer`, { text });
    $("answer").value = ""; render(v); $("status").textContent = "";
    if (!v.done) $("answer").focus();
  } catch (e) { $("status").textContent = e.message; }
  $("send").disabled = false;
}
$("answer-form").addEventListener("submit", send);
$("answer").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(e); });

$("confirm").addEventListener("click", async () => {
  $("confirm").disabled = true;
  try {
    await api(`/api/needs/${need.need_id}/confirm`, { version: need.version });
    $("confirm").textContent = "Confirmed"; $("preview").hidden = false; $("preview").scrollIntoView();
    const p = await api(`/api/needs/${need.need_id}/preview`, null, "GET");
    $("preview-status").textContent = `Registered so far: ${p.registered_so_far} jobseekers · as of ${p.as_of} · Need Card ${p.need_id} v${p.version}`;
    $("preview-sentence").textContent = p.sentence;
    const label = { evidence_for_most_must_haves: "Evidence for most of your must-haves", and_start_window_and_language: "…and can start in your window and meet your language requirement", and_work_mode: "…and fit your work mode" };
    $("preview-groups").replaceChildren(...Object.entries(p.counts).map(([k, v]) => {
      const li = document.createElement("li"); li.textContent = `${label[k]}: ${typeof v === "number" ? "about " + v : v}`; return li;
    }));
    $("preview-narrow").textContent = p.narrowing.length ? `These requirements narrow your pool most: ${p.narrowing.join("; ")}.` : "";
    $("preview-frame").textContent = p.framing;
  } catch (e) { $("preview-status").textContent = e.message; $("confirm").disabled = false; }
});
