const token = window.SPARK_PASS_TOKEN;
const key = `spark-pass:${token}`;
const queueKey = `spark-pulse:${token}`;
const tappedKey = `spark-pulse-tapped:${token}`;
const content = document.getElementById("pass-content");
const state = document.getElementById("pass-state");

function element(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value || "";
  if (className) node.className = className;
  return node;
}

function draw(payload) {
  content.replaceChildren();
  if (payload.expires_at && payload.expires_at * 1000 <= Date.now()) {
    document.getElementById("expired").hidden = false;
    state.textContent = "Expired";
    return;
  }
  const queued = JSON.parse(localStorage.getItem(queueKey) || "[]");
  const tappedSlots = new Set([...JSON.parse(localStorage.getItem(tappedKey) || "[]"), ...queued.map((item) => item.slot_id)]);
  payload.agenda.forEach((slot) => {
    const card = document.createElement("article");
    card.className = "card";
    card.append(element("h2", `${slot.time} · ${slot.company}`), element("p", `${slot.industry} · Need ${slot.need_id}`, "muted"),
      element("p", slot.why));
    const list = document.createElement("ol");
    slot.questions.forEach((item) => {
      const row = document.createElement("li");
      row.append(element("p", item.question), element("small", `Evidence: ${item.evidence} · Sources: ${item.source_ids.join(", ")}`, "muted"));
      list.append(row);
    });
    card.append(list);
    (slot.conflicts || []).forEach((item) => card.append(element("p", `Constraint to discuss: ${item.reason}`, "notice")));
    const actions = document.createElement("div");
    actions.className = "actions";
    for (const response of payload.pulse_choices) {
      const button = element("button", response === "useful" ? "Useful" : "Needs improvement", "ghost");
      button.disabled = tappedSlots.has(slot.slot_id);
      button.addEventListener("click", () => {
        if (tappedSlots.has(slot.slot_id)) return;
        tappedSlots.add(slot.slot_id);
        localStorage.setItem(tappedKey, JSON.stringify([...tappedSlots]));
        actions.querySelectorAll("button").forEach((choice) => { choice.disabled = true; });
        const queue = JSON.parse(localStorage.getItem(queueKey) || "[]");
        queue.push({ client_event_id: crypto.randomUUID().replaceAll("-", ""), slot_id: slot.slot_id, response });
        localStorage.setItem(queueKey, JSON.stringify(queue));
        document.getElementById("pulse-state").textContent = "Feedback saved on this device. It will sync when you are online.";
        sync();
      });
      actions.append(button);
    }
    if (tappedSlots.has(slot.slot_id)) {
      actions.setAttribute("aria-label", "Feedback already saved for this conversation");
    }
    card.append(actions);
    content.append(card);
  });
  state.textContent = `${payload.agenda.length} conversations · ${payload.date} · ${payload.venue}${navigator.onLine ? "" : " · offline copy"}`;
}

async function load() {
  try {
    const response = await fetch(`/api/passes/${encodeURIComponent(token)}`);
    const payload = await response.json();
    if (response.status === 410) {
      document.getElementById("expired").hidden = false;
      state.textContent = "Expired";
      return;
    }
    if (!response.ok) {
      state.textContent = payload.detail || "Pass is unavailable.";
      return;
    }
    localStorage.setItem(key, JSON.stringify(payload));
    draw(payload);
    sync();
  } catch (error) {
    const cached = localStorage.getItem(key);
    if (!cached) { state.textContent = "No offline copy is saved on this device yet."; return; }
    draw(JSON.parse(cached));
    sync();
  }
}

async function sync() {
  const queue = JSON.parse(localStorage.getItem(queueKey) || "[]");
  if (!queue.length) { document.getElementById("pulse-state").textContent = "No feedback waiting to sync."; return; }
  const remaining = [];
  let rejected = 0;
  for (const item of queue) {
    try {
      const response = await fetch(`/api/passes/${encodeURIComponent(token)}/pulse`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item),
      });
      if (response.status === 404 || response.status === 410) { rejected += 1; continue; }
      if (!response.ok) { remaining.push(item); continue; }
    } catch { remaining.push(item); }
  }
  localStorage.setItem(queueKey, JSON.stringify(remaining));
  document.getElementById("pulse-state").textContent = remaining.length ? `${remaining.length} feedback item(s) will retry later.` :
    rejected ? `${rejected} item(s) could not sync because this pass expired or was replaced.` : "Feedback synced.";
}

document.getElementById("sync-pulse").addEventListener("click", sync);
window.addEventListener("online", sync);
load();
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.ready.then((registration) => {
    registration.active?.postMessage({ type: "CACHE_PASS_PAGE", url: window.location.href });
  }).catch(() => {});
}
