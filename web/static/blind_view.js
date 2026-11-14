const $ = (id) => document.getElementById(id);

function text(node, value) { node.textContent = value || ""; return node; }

function coverage(item) {
  const row = document.createElement("li");
  const evidence = item.evidence === "supported" ? "Direct evidence" : item.evidence === "partial" ? "Partial evidence" :
    item.evidence === "adjacent" ? "Related experience" : "No evidence yet";
  row.append(text(document.createElement("strong"), `${evidence}: `), document.createTextNode(item.outcome));
  if (item.excerpt) row.append(document.createElement("br"), text(document.createElement("q"), item.excerpt));
  if (item.reason) row.append(document.createElement("span"), ` — ${item.reason}`);
  return row;
}

function cardView(card) {
  const article = document.createElement("article");
  article.className = "card blind-card";
  article.append(text(document.createElement("h2"), card.tier_label));
  article.append(text(document.createElement("p"), card.why));
  const preferences = document.createElement("p");
  preferences.className = "muted";
  preferences.textContent = `Practical preferences: available from ${card.practical_preferences.start}; ${card.practical_preferences.work_mode} work.`;
  article.append(preferences);
  const list = document.createElement("ul");
  list.className = "coverage";
  card.coverage.forEach((item) => list.append(coverage(item)));
  article.append(list);
  const conversation = document.createElement("section");
  conversation.className = "notice";
  conversation.hidden = true;
  const prompts = document.createElement("div");
  const promptButton = document.createElement("button");
  promptButton.className = "ghost";
  promptButton.textContent = "Open conversation prompts";
  promptButton.addEventListener("click", async () => {
    promptButton.disabled = true;
    try {
      const response = await fetch(`/api/blind-matches/${encodeURIComponent(card.match_id)}/conversation-card`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Conversation prompts are unavailable.");
      const fragment = document.createDocumentFragment();
      body.questions.forEach((item) => {
        const row = document.createElement("p");
        row.append(document.createTextNode(item.question));
        const refs = document.createElement("small");
        refs.className = "muted";
        refs.textContent = ` Sources: ${item.source_ids.join(", ")}`;
        row.append(document.createElement("br"), refs);
        fragment.append(row);
      });
      body.conflicts.forEach((item) => {
        const row = document.createElement("p");
        row.className = "muted";
        row.textContent = `Practical constraint to discuss: ${item.reason}`;
        fragment.append(row);
      });
      prompts.replaceChildren(fragment);
      conversation.hidden = false;
    } catch (error) {
      prompts.textContent = error.message;
      conversation.hidden = false;
    } finally { promptButton.disabled = false; }
  });
  article.append(promptButton, conversation);
  conversation.append(prompts);
  const status = document.createElement("p");
  status.className = "muted";
  const button = document.createElement("button");
  button.className = "primary";
  button.textContent = card.reveal_state === "revealed" ? "Check reveal status" : "Ask to reveal contact";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch(card.reveal_state === "revealed" ? `/api/blind-matches/${card.match_id}/reveal?role=recruiter` :
        `/api/blind-matches/${card.match_id}/opt-in/recruiter`,
        { method: card.reveal_state === "revealed" ? "GET" : "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not update this request.");
      if (body.revealed) {
        status.textContent = `Both people opted in. Contact: ${body.reveal.name} (${body.reveal.email}).`;
        button.hidden = true;
      } else {
        status.textContent = "Your opt-in is recorded. Contact details remain hidden until the jobseeker also opts in.";
        card.reveal_state = body.state;
        button.textContent = "Check reveal status";
      }
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  article.append(button, status);
  return article;
}

$("load-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const needId = $("need-id").value.trim().toUpperCase();
  const status = $("load-status");
  status.textContent = "Loading redacted evidence cards…";
  try {
    const response = await fetch(`/api/needs/${encodeURIComponent(needId)}/blind-candidates`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Could not open blind cards.");
    $("framing").textContent = body.framing;
    const cards = $("cards");
    cards.replaceChildren(...body.candidates.map(cardView));
    $("cards-section").hidden = false;
    status.textContent = `${body.candidates.length} registered people are viewable without identity details.`;
  } catch (error) {
    $("cards-section").hidden = true;
    status.textContent = error.message;
  }
});
