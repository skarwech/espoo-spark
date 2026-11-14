const $ = (id) => document.getElementById(id);
let draft = null;

async function request(path, method = "GET", body) {
  const headers = { "X-Organizer-Token": $("organizer-token").value };
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Request failed.");
  return result;
}

function renderAgenda(agenda) {
  const table = document.createElement("table");
  const header = document.createElement("tr");
  for (const item of ["Time", "Company", "Need", "Evidence", "Trace"]) {
    const cell = document.createElement("th"); cell.textContent = item; header.append(cell);
  }
  table.append(header);
  for (const slot of agenda.slots) {
    const row = document.createElement("tr");
    for (const value of [slot.time, slot.company, slot.need_id, slot.tier, slot.why]) {
      const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
    }
    table.append(row);
  }
  $("agenda-table").replaceChildren(table);
  const selector = $("trace-slot");
  selector.replaceChildren(...agenda.slots.map((slot) => {
    const option = document.createElement("option");
    option.value = slot.slot_id;
    option.textContent = `${slot.time} · ${slot.company}`;
    return option;
  }));
  $("agenda-summary").textContent = `${agenda.slots.length} meetings · ${agenda.people_with_two_slots} people have two meetings · ${agenda.date}. ${agenda.approved ? "Approved." : "Draft only until approval."}`;
  $("agenda-section").hidden = false;
}

$("event-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = $("event-id").value.trim();
  try {
    draft = await request(`/api/events/${encodeURIComponent(id)}/agenda`, "POST", { date: $("event-date").value, start_time: $("start-time").value });
    renderAgenda(draft);
    $("event-status").textContent = "Draft saved. Review the full agenda before approval.";
    $("approve-event").disabled = false;
    $("issue-passes").disabled = true;
  } catch (error) { $("event-status").textContent = error.message; }
});

$("load-agenda").addEventListener("click", async () => {
  const id = $("event-id").value.trim();
  try {
    const saved = await request("/api/events/" + encodeURIComponent(id) + "/agenda");
    draft = saved;
    renderAgenda(saved);
    $("event-status").textContent = saved.approved ? "This agenda is approved." : "This agenda is a draft and needs review.";
    $("approve-event").disabled = saved.approved;
    $("issue-passes").disabled = !saved.approved || saved.passes_issued;
  } catch (error) { $("event-status").textContent = error.message; }
});

$("approve-event").addEventListener("click", async () => {
  try {
    await request(`/api/events/${encodeURIComponent($("event-id").value)}/approve`, "POST");
    $("event-status").textContent = "Agenda approved. Event passes can now be issued.";
    $("approve-event").disabled = true;
    $("issue-passes").disabled = false;
  } catch (error) { $("event-status").textContent = error.message; }
});

$("issue-passes").addEventListener("click", async () => {
  try {
    const body = await request(`/api/events/${encodeURIComponent($("event-id").value)}/passes`, "POST");
    const list = document.createElement("ul");
    body.passes.forEach((pass) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/event-pass/${encodeURIComponent(pass.token)}`;
      link.textContent = `Attendee ${pass.attendee_ref} · ${pass.slot_ids.length} meeting(s) · expires ${new Date(pass.expires_at * 1000).toLocaleDateString()}`;
      item.append(link); list.append(item);
    });
    $("pass-links").replaceChildren(list);
    $("issue-passes").disabled = true;
    $("event-status").textContent = `${body.passes.length} private pass(es) issued. Store each link securely and deliver it to its attendee.`;
  } catch (error) { $("event-status").textContent = error.message; }
});

$("dashboard").addEventListener("click", async () => {
  try {
    const body = await request(`/api/organizer/events/${encodeURIComponent($("event-id").value)}/dashboard`);
    const block = document.createElement("pre"); block.className = "mono"; block.textContent = JSON.stringify(body, null, 2);
    $("dashboard-content").replaceChildren(block);
  } catch (error) { $("event-status").textContent = error.message; }
});

$("trace-slot-button").addEventListener("click", async () => {
  try {
    const eventId = encodeURIComponent($("event-id").value);
    const slotId = encodeURIComponent($("trace-slot").value);
    const body = await request(`/api/organizer/events/${eventId}/trace/${slotId}`);
    const block = document.createElement("pre"); block.className = "mono"; block.textContent = JSON.stringify(body, null, 2);
    $("trace-content").replaceChildren(block);
  } catch (error) { $("event-status").textContent = error.message; }
});

$("bias-audit").addEventListener("click", async () => {
  try {
    const body = await request("/api/organizer/bias-audit");
    const block = document.createElement("pre"); block.className = "mono"; block.textContent = JSON.stringify(body, null, 2);
    $("audit-content").replaceChildren(block);
  } catch (error) { $("event-status").textContent = error.message; }
});

$("compliance").addEventListener("click", async () => {
  try {
    const body = await request("/api/organizer/compliance");
    const block = document.createElement("pre"); block.className = "mono"; block.textContent = JSON.stringify(body, null, 2);
    $("compliance-content").replaceChildren(block);
  } catch (error) { $("event-status").textContent = error.message; }
});
