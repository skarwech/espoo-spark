// Skill Story (F3). The text you type is sent exactly as typed; the AI only asks questions.
const $ = (id) => document.getElementById(id);
const BOXES = ["problem", "did", "differently", "next"];
const MAX = 120;
const words = (t) => (t.trim() ? t.trim().split(/\s+/).length : 0);
const lang = () => ($("lang").value === "other" ? $("lang-other").value.trim().toLowerCase() : $("lang").value);

async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

$("lang").addEventListener("change", () => { $("lang-other-wrap").hidden = $("lang").value !== "other"; });
for (const b of BOXES) {
  $(b).addEventListener("input", () => {
    const n = words($(b).value);
    $(`${b}-count`).textContent = `${n} / ${MAX} words${n > MAX ? " (too long)" : ""}`;
    $(`${b}-count`).style.color = n > MAX ? "var(--bad)" : "";
  });
}

document.querySelectorAll(".clarify").forEach((btn) => btn.addEventListener("click", async () => {
  const b = btn.dataset.box, ul = $(`${b}-q`);
  if (ul.children.length >= 2) return;
  btn.disabled = true;
  try {
    const { questions } = await api("/api/stories/clarify", { box: b, text: $(b).value, lang: lang() });
    const items = questions.length ? questions : ["This box already says what you did and how. No question."];
    ul.replaceChildren(...items.slice(0, 2).map((q) => { const li = document.createElement("li"); li.dir = "auto"; li.textContent = q; return li; }));
  } catch (e) { ul.replaceChildren(Object.assign(document.createElement("li"), { textContent: e.message })); }
  btn.disabled = false;
}));

$("check").addEventListener("click", async () => {
  const res = await api("/api/stories/check", Object.fromEntries(BOXES.map((b) => [b, $(b).value])));
  const items = BOXES.flatMap((b) => res[b].map((w) => `Box ${BOXES.indexOf(b) + 1}: “${w.match}” looks like a ${w.category}. Consider: ${w.suggestion}.`));
  $("id-warnings").replaceChildren(...(items.length ? items : ["No personal details found. You decide what to keep."]).map((t) => {
    const li = document.createElement("li"); li.textContent = t; if (!items.length) li.className = "ok"; return li;
  }));
});

function payload() {
  const languages = [...document.querySelectorAll("#langs select")].filter((s) => s.value).map((s) => ({ lang: s.dataset.lang, level: s.value }));
  return {
    lang: lang(), ...Object.fromEntries(BOXES.map((b) => [b, $(b).value])), ai_used: $("ai_used").checked,
    constraints: { languages, start: $("start").value, work_mode: $("work_mode").value, needs_relocation: $("needs_relocation").checked },
    mini_case: $("mini_case").value || undefined, private_context: $("private_context").value || undefined, consent: $("consent").checked,
  };
}

$("preview-btn").addEventListener("click", () => {
  const p = payload(), box = $("recruiter-preview");
  const titles = ["A problem I solved", "What I did", "What I'd do differently", "What I want next"];
  box.replaceChildren(Object.assign(document.createElement("h3"), { textContent: "What recruiters will see (no name, no photo, no score)" }),
    ...BOXES.map((b, i) => { const p2 = document.createElement("p"); p2.dir = "auto"; p2.innerHTML = `<b></b> `; p2.firstChild.textContent = titles[i] + ":"; p2.append(p[b] || "—"); return p2; }),
    Object.assign(document.createElement("p"), { className: "muted", textContent: `Languages: ${p.constraints.languages.map((l) => l.lang + " " + l.level).join(", ") || "—"} · ${p.constraints.work_mode} · from ${p.constraints.start}` }));
  box.hidden = false;
});

$("story").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("submit").disabled = true; $("submit-status").textContent = "Saving…";
  try {
    const { story_id } = await api("/api/stories", payload());
    localStorage.setItem("spark-delete-code", story_id);
    $("submit-status").textContent = `Saved exactly as written (reference ${story_id}). Thank you.`;
    $("delete-story").hidden = false;
  } catch (err) { $("submit-status").textContent = err.message; }
  $("submit").disabled = false;
});

$("delete-story-button").addEventListener("click", async () => {
  const storyId = localStorage.getItem("spark-delete-code");
  if (!storyId || !confirm("Delete your saved Skill Story and its private context? This cannot be undone.")) return;
  const response = await fetch(`/api/stories/${encodeURIComponent(storyId)}`, { method: "DELETE", headers: { "X-Delete-Code": storyId } });
  const body = await response.json();
  if (!response.ok) { $("submit-status").textContent = body.detail || "Deletion failed."; return; }
  localStorage.removeItem("spark-delete-code");
  $("submit-status").textContent = "Your saved story and its cached extraction were deleted.";
  $("delete-story").hidden = true;
});
