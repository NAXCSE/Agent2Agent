const state = {
  people: [],
  result: null,
  activeIndex: null,
};

const $ = (id) => document.getElementById(id);

// Where the API lives. config.js sets this for Vercel; ?api= overrides both.
const API_BASE = (() => {
  const fromQuery = new URLSearchParams(location.search).get("api");
  if (fromQuery) return fromQuery.replace(/\/$/, "");
  const configured = (window.AGENT2AGENT_API || "").replace(/\/$/, "");
  return configured;
})();

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => (el.className = "toast"), 4000);
}

async function api(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch (err) {
    payload = { detail: text };
  }
  if (!response.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) ||
      `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

async function loadHealth() {
  const el = $("health");
  try {
    const health = await api("/api/health");
    const where = API_BASE ? API_BASE.replace(/^https?:\/\//, "") : "this origin";
    el.innerHTML = health.llm_ready
      ? `API ${where} &middot; LLM <b class="ok">${health.llm_model}</b>`
      : `API ${where} &middot; LLM <b class="off">not configured</b> (fallback mode)`;
  } catch (err) {
    el.innerHTML = `<b class="off">API unreachable</b>`;
  }
}

function renderPeople() {
  const box = $("people");
  const males = state.people.filter((p) => p.gender === "male").length;
  const females = state.people.length - males;

  $("poolCount").textContent = state.people.length;
  $("poolInfo").textContent = state.people.length
    ? `${state.people.length} people loaded (${males} male, ${females} female).`
    : "Add a profile or load the demo pool.";

  box.innerHTML = state.people
    .map(
      (person) => `<div class="person ${person.gender}">
        <div>${person.gender === "male" ? "♂" : "♀"} ${person.name}</div>
        <div class="role">${person.headline || ""}</div>
      </div>`
    )
    .join("");
}

function settings() {
  const ageGap = $("maxAgeGap").value.trim();
  return {
    rounds: Number($("rounds").value) || 4,
    shortlist_per_person: Number($("shortlist").value) || 3,
    max_conversations: Number($("maxConv").value) || 8,
    min_compatibility: Number($("minComp").value) || 0,
    max_age_gap: ageGap === "" ? null : Number(ageGap),
    judge: $("judge").checked,
  };
}

async function loadDemo() {
  try {
    $("loadDemo").disabled = true;
    state.people = await api("/api/demo-people");
    renderPeople();
    toast(`Loaded ${state.people.length} demo people`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    $("loadDemo").disabled = false;
  }
}

function personFieldset(index) {
  const removable = index >= MIN_PEOPLE;
  return `<fieldset data-index="${index}">
    <legend>Person ${index + 1}</legend>
    <div class="personHead">
      <span class="muted small">LinkedIn + Instagram of the same person</span>
      ${removable ? `<button class="secondary tiny" data-remove="${index}">remove</button>` : ""}
    </div>
    <label>LinkedIn URL
      <input type="text" data-li="${index}"
        placeholder="https://www.linkedin.com/in/..." />
    </label>
    <label>Instagram URL
      <input type="text" data-ig="${index}"
        placeholder="https://www.instagram.com/..." />
    </label>
  </fieldset>`;
}

function renderPersonForms() {
  const box = $("personForms");
  if (box.childElementCount < MIN_PEOPLE) {
    box.innerHTML = personFieldset(0) + personFieldset(1);
  } else {
    box.insertAdjacentHTML("beforeend", personFieldset(box.childElementCount));
  }
  bindPersonFormEvents();
}

function bindPersonFormEvents() {
  $("personForms")
    .querySelectorAll("[data-remove]")
    .forEach((button) => {
      button.onclick = () => {
        button.closest("fieldset").remove();
        bindPersonFormEvents();
      };
    });
}

// Reads every person form. Returns { complete, missing, people }.
function collectPeopleInputs() {
  const entries = [];

  $("personForms").querySelectorAll("fieldset").forEach((fieldset) => {
    const index = fieldset.dataset.index;
    entries.push({
      linkedin: fieldset.querySelector(`[data-li="${index}"]`).value,
      instagram: fieldset.querySelector(`[data-ig="${index}"]`).value,
    });
  });

  return collectPeople(entries);
}

async function analyze() {
  const { complete, missing, people } = collectPeopleInputs();

  if (missing.length) {
    toast(missing[0], true);
    return;
  }
  if (people.length < MIN_PEOPLE) {
    toast(`Need at least ${MIN_PEOPLE} people, one LinkedIn + Instagram URL each`, true);
    return;
  }

  const button = $("analyze");
  button.disabled = true;
  const added = [];

  try {
    for (let i = 0; i < people.length; i += 1) {
      const person = people[i];
      toast(`Analyzing person ${i + 1} of ${people.length}…`);
      // Sequential on purpose: each call runs two paid Actors plus one LLM call.
      const result = await api("/api/people/analyze", {
        method: "POST",
        body: JSON.stringify(person),
      });
      added.push(result);
    }
    state.people = [...state.people, ...added];
    renderPeople();
    $("personForms").innerHTML = personFieldset(0) + personFieldset(1);
    bindPersonFormEvents();
    toast(
      `Added ${added.length}: ` +
        added.map((p) => `${p.name} (${p.gender})`).join(", ")
    );
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function run() {
  if (state.people.length < 2) {
    toast("Need at least two people", true);
    return;
  }
  try {
    $("run").disabled = true;
    toast("Shortlisting, conversing and judging…");
    state.result = await api("/api/matchmaking", {
      method: "POST",
      body: JSON.stringify({ people: state.people, settings: settings() }),
    });
    state.activeIndex = null;
    renderRanking();
    renderConversation();
    const r = state.result;
    toast(
      `${r.conversations_held} conversations, ${r.pairs_shortlisted}/${r.total_pairs_considered} pairs shortlisted`
    );
  } catch (err) {
    toast(err.message, true);
  } finally {
    $("run").disabled = false;
  }
}

const EXCLUSION_LABELS = {
  same_gender: "same gender (male/female only)",
  deal_breaker: "stated deal breakers",
  age_gap: "age gap too large",
  other: "other",
};

function emptyState(r) {
  const summary = r.exclusion_summary || {};
  const counts = Object.entries(summary)
    .map(([key, value]) => `<li>${value} &times; ${EXCLUSION_LABELS[key] || key}</li>`)
    .join("");

  const hints = (r.suggestions || [])
    .map((text) => `<li>${text}</li>`)
    .join("");

  return `<div class="emptyState">
    <h3>No viable matches</h3>
    <p class="muted">
      ${r.total_pairs_considered} pair(s) considered,
      ${r.pairs_shortlisted} shortlisted,
      ${r.conversations_held} conversation(s) held &mdash; nothing survived.
      No LLM calls were spent on the excluded pairs.
    </p>
    ${counts ? `<p class="muted small">Excluded because of:</p><ul>${counts}</ul>` : ""}
    ${hints ? `<p class="muted small">Try:</p><ul>${hints}</ul>` : ""}
    <p class="muted small">
      Raise the max age gap, lower min compatibility, add more people, or review
      the deal breakers in the AI profiles.
    </p>
  </div>`;
}

function renderRanking() {
  const box = $("ranking");
  const r = state.result;
  if (!r) {
    box.innerHTML = "";
    return;
  }

  $("resultMeta").textContent = `${r.conversations_held} conversations`;

  if (!r.eligible_matches.length) {
    box.innerHTML = emptyState(r);
    return;
  }

  const rows = r.eligible_matches
    .map((match, index) => {
      const conversed = Boolean(match.conversation);
      const verdict = match.verdict;
      const ground = verdict ? verdict.common_ground.length : 0;
      const messages = match.conversation ? match.conversation.turns.length : 0;
      return `<div class="match" data-index="${index}">
        <div class="rowline">
          <span>${index + 1}. ${match.person_a_name} + ${match.person_b_name}</span>
          <span class="score">${match.final_score}</span>
        </div>
        <div class="meta">
          ${match.gender_pair} &middot; compat ${match.compatibility_score}
          ${conversed ? ` &middot; ${messages} messages &middot; ${ground} common topics` : " &middot; not conversed"}
          ${verdict ? ` &middot; judged by ${verdict.judged_by}` : ""}
        </div>
        <div class="bar"><i style="width:${Math.min(100, match.final_score)}%"></i></div>
      </div>`;
    })
    .join("");

  const excluded = r.excluded_pairs.length
    ? `<div class="muted small" style="margin-top:10px">
        ${r.excluded_pairs.length} pair(s) excluded: same gender or deal breakers.
       </div>`
    : "";

  box.innerHTML = rows + excluded;

  box.querySelectorAll(".match").forEach((el) => {
    el.addEventListener("click", () => {
      state.activeIndex = Number(el.dataset.index);
      renderRanking();
      renderConversation();
    });
  });
}

function renderConversation() {
  const box = $("conversation");
  const r = state.result;

  if (!r || state.activeIndex === null) {
    box.innerHTML =
      '<div class="empty">Run matchmaking, then pick a pair to read the conversation.</div>';
    return;
  }

  const match = r.eligible_matches[state.activeIndex];
  document
    .querySelectorAll(".match")
    .forEach((el) => el.classList.toggle("active", Number(el.dataset.index) === state.activeIndex));

  if (!match.conversation) {
    box.innerHTML = `<div class="empty">
      ${match.person_a_name} + ${match.person_b_name} were shortlisted but no conversation
      was held (cap: ${r.settings.max_conversations}). Raise the conversation cap to
      include this pair.
    </div>`;
    return;
  }

  const turns = match.conversation.turns
    .map((turn) => {
      const isA = turn.speaker_agent_id === match.person_a_id;
      const who = isA ? match.person_a_name : match.person_b_name;
      return `<div class="bubble ${isA ? "a" : "b"}">
        <div class="who">${who}</div>${turn.message}
      </div>`;
    })
    .join("");

  const verdict = match.verdict;
  const dims = verdict
    ? `<div class="dims">
        ${[
          ["interesting", verdict.interestingness],
          ["depth", verdict.depth],
          ["chemistry", verdict.chemistry],
        ]
          .map(
            ([label, value]) =>
              `<div><span>${label}</span>${Math.round(value * 100)}%</div>`
          )
          .join("")}
      </div>
      <div>${verdict.verdict || ""}</div>
      ${
        verdict.common_ground.length
          ? `<div style="margin-top:6px">common ground:
             ${verdict.common_ground.map((g) => `<span class="chip ground">${g}</span>`).join("")}</div>`
          : ""
      }
      ${
        verdict.red_flags.length
          ? `<div style="margin-top:6px">red flags:
             ${verdict.red_flags.map((f) => `<span class="chip flag">${f}</span>`).join("")}</div>`
          : ""
      }`
    : '<div class="muted small">No judgement for this conversation.</div>';

  const breakdown = Object.entries(match.score_breakdown || {})
    .map(([key, value]) => `<span class="chip">${key} ${value}</span>`)
    .join("");

  box.innerHTML = `
    <div class="rowline"><strong>${match.person_a_name} + ${match.person_b_name}</strong>
      <span class="score">${match.final_score}</span></div>
    <div class="meta muted small">${match.gender_pair} &middot; ${breakdown}</div>
    <div class="chat" style="margin-top:12px">${turns}</div>
    <div class="verdict">
      <div class="muted small" style="text-transform:uppercase;letter-spacing:1px">
        judgement${verdict ? " (judged by " + verdict.judged_by + ")" : ""}
      </div>
      ${dims}
    </div>`;
}

$("loadDemo").addEventListener("click", loadDemo);
$("analyze").addEventListener("click", analyze);
$("addPerson").addEventListener("click", renderPersonForms);
$("run").addEventListener("click", run);
$("reset").addEventListener("click", () => {
  state.people = [];
  state.result = null;
  state.activeIndex = null;
  $("personForms").innerHTML = personFieldset(0) + personFieldset(1);
  bindPersonFormEvents();
  renderPeople();
  renderRanking();
  renderConversation();
});

loadHealth();
renderPeople();
renderPersonForms();