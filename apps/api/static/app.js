/* Vault Zeta console — vanilla SPA.
 * Same-origin as the API, so all calls are relative. Auth is a bearer token
 * kept in localStorage; SSE uses fetch + ReadableStream so we can set the
 * Authorization header (EventSource can't). */

const KEY_NAME = "vault_api_key";
const SESSION_NAME = "vault_session_id";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

// ── auth / fetch ──────────────────────────────────────────────────
const getKey = () => localStorage.getItem(KEY_NAME) || "";
const setKey = (k) => k ? localStorage.setItem(KEY_NAME, k) : localStorage.removeItem(KEY_NAME);

function authHeaders(extra = {}) {
  const k = getKey();
  return k ? { ...extra, Authorization: `Bearer ${k}` } : { ...extra };
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: authHeaders(opts.headers || {}),
  });
  if (res.status === 401) {
    setConn(false);
    openModal("Unauthorized — set a valid API key.");
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${body.slice(0, 200)}`);
  }
  return res;
}

const apiJson = async (path, opts) => (await api(path, opts)).json();

// ── connection indicator ──────────────────────────────────────────
function setConn(ok) {
  const dot = $("#conn-dot");
  dot.className = "dot " + (ok === null ? "dot-idle" : ok ? "dot-ok" : "dot-bad");
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), 3200);
}

// ── tabs ──────────────────────────────────────────────────────────
const loaded = { memory: false, system: false };
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    const name = tab.dataset.tab;
    $(`#panel-${name}`).classList.add("active");
    if (name === "memory" && !loaded.memory) { loadMemory(); loaded.memory = true; }
    if (name === "system" && !loaded.system) { loadSystem(); loaded.system = true; }
  });
});

// ── identity header ───────────────────────────────────────────────
async function loadIdentity() {
  try {
    const d = await apiJson("/v1/console/identity");
    $("#mission-text").textContent = d.mission;
    $("#mission-days").textContent = `${d.days_remaining} days`;
    $("#mission-model").textContent = `${d.model}`;
    $("#mission").hidden = false;
    setConn(true);
  } catch (e) {
    if (e.message !== "unauthorized") setConn(false);
  }
}

// ── chat ──────────────────────────────────────────────────────────
const chatLog = $("#chat-log");
const chatInput = $("#chat-input");
let streaming = false;

function clearEmptyHint() {
  const hint = chatLog.querySelector(".empty-hint");
  if (hint) hint.remove();
}

function addMessage(role, text = "") {
  clearEmptyHint();
  const msg = el("div", `msg ${role}`);
  msg.appendChild(el("span", "who", role === "user" ? "you" : "scrappy"));
  const steps = el("div", "steps");
  msg.appendChild(steps);
  const bubble = el("div", "bubble");
  bubble.textContent = text;
  msg.appendChild(bubble);
  chatLog.appendChild(msg);
  chatLog.scrollTop = chatLog.scrollHeight;
  return { msg, bubble, steps };
}

function addStep(steps, kind, text) {
  const chip = el("div", `chip chip-${kind}`, text);
  steps.appendChild(chip);
  chatLog.scrollTop = chatLog.scrollHeight;
}

// Parse a single SSE block ("event: x\ndata: y\ndata: z") → {event, data}.
function parseSSE(block) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // comment / ping
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  return { event, data: dataLines.join("\n") };
}

async function sendChat(message) {
  if (streaming) return;
  streaming = true;
  $("#chat-send").disabled = true;
  addMessage("user", message);
  const { bubble, steps } = addMessage("assistant", "");
  bubble.classList.add("cursor");

  let sessionId = localStorage.getItem(SESSION_NAME);
  try {
    const res = await api("/v1/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        message,
        channel: "web",
        session_id: sessionId || null,
      }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let answer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const { event, data } = parseSSE(block);
        if (event === "token") {
          answer += data;
          bubble.textContent = answer;
          chatLog.scrollTop = chatLog.scrollHeight;
        } else if (event === "session") {
          const d = JSON.parse(data);
          localStorage.setItem(SESSION_NAME, d.session_id);
        } else if (event === "tool_call") {
          const d = JSON.parse(data);
          const arg = JSON.stringify(d.arguments);
          addStep(steps, "call", `→ ${d.name} ${arg.length > 60 ? arg.slice(0, 60) + "…" : arg}`);
        } else if (event === "status") {
          const d = JSON.parse(data);
          addStep(steps, "result", `✓ ${d.tool}: ${d.result}`);
        } else if (event === "error") {
          const d = JSON.parse(data);
          addStep(steps, "call", `error: ${d.error}`);
          toast("Stream error: " + d.error, true);
        }
        // "done" needs no UI action beyond ending the loop.
      }
    }
    if (!answer) bubble.textContent = "(no response)";
    setConn(true);
  } catch (e) {
    bubble.textContent = bubble.textContent || "(failed)";
    if (e.message !== "unauthorized") toast("Chat failed: " + e.message, true);
  } finally {
    bubble.classList.remove("cursor");
    streaming = false;
    $("#chat-send").disabled = false;
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const msg = chatInput.value.trim();
  if (!msg) return;
  chatInput.value = "";
  chatInput.style.height = "auto";
  sendChat(msg);
});

// Enter to send, Shift+Enter for newline; autosize.
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#chat-form").requestSubmit();
  }
});
chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
});

$("#chat-new").addEventListener("click", () => {
  localStorage.removeItem(SESSION_NAME);
  chatLog.innerHTML = "";
  const hint = el("div", "empty-hint", "New session. Ask Scrappy anything.");
  chatLog.appendChild(hint);
  toast("Started a fresh session.");
});

// ── memory ────────────────────────────────────────────────────────
const memList = $("#mem-list");

function memItem(m, { showSim } = {}) {
  const item = el("div", "mem-item");
  const body = el("div", "body");
  body.appendChild(el("div", "content", m.content));
  const meta = el("div", "meta");
  meta.appendChild(el("span", "badge", m.kind));
  meta.appendChild(el("span", null, `importance ${m.importance}`));
  if (showSim && m.similarity != null) {
    meta.appendChild(el("span", "sim", `sim ${m.similarity}`));
  }
  if (m.source) meta.appendChild(el("span", null, m.source));
  if (m.use_count != null) meta.appendChild(el("span", null, `used ${m.use_count}×`));
  if (m.created_at) meta.appendChild(el("span", null, new Date(m.created_at).toLocaleDateString()));
  body.appendChild(meta);
  item.appendChild(body);

  const del = el("button", "mem-del", "✕");
  del.title = "delete";
  del.addEventListener("click", async () => {
    if (!confirm("Delete this memory?")) return;
    try {
      await api(`/v1/console/memory/${m.id}`, { method: "DELETE" });
      item.remove();
      toast("Memory deleted.");
    } catch (e) {
      toast("Delete failed: " + e.message, true);
    }
  });
  item.appendChild(del);
  return item;
}

async function loadMemory() {
  $("#mem-meta").textContent = "Loading…";
  try {
    const d = await apiJson("/v1/console/memory?limit=100");
    renderMemList(d.memories, `${d.total} memories stored · showing ${d.memories.length}`);
  } catch (e) {
    $("#mem-meta").textContent = "Failed to load: " + e.message;
  }
}

function renderMemList(items, metaText, opts = {}) {
  $("#mem-meta").textContent = metaText;
  memList.innerHTML = "";
  if (!items.length) {
    memList.appendChild(el("div", "muted", "Nothing here yet."));
    return;
  }
  items.forEach((m) => memList.appendChild(memItem(m, opts)));
}

$("#mem-search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#mem-search-input").value.trim();
  if (!q) return loadMemory();
  $("#mem-meta").textContent = "Searching…";
  try {
    const d = await apiJson("/v1/console/memory/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: q, k: 20 }),
    });
    renderMemList(d.results, `${d.results.length} matches for "${q}" (by meaning)`, { showSim: true });
  } catch (e2) {
    $("#mem-meta").textContent = "Search failed: " + e2.message;
  }
});

$("#mem-refresh").addEventListener("click", () => {
  $("#mem-search-input").value = "";
  loadMemory();
});

$("#mem-add-toggle").addEventListener("click", () => {
  $("#mem-add-form").hidden = !$("#mem-add-form").hidden;
});
$("#mem-add-importance").addEventListener("input", (e) => {
  $("#mem-add-importance-val").textContent = Number(e.target.value).toFixed(2);
});

$("#mem-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = $("#mem-add-content").value.trim();
  if (!content) return;
  try {
    await api("/v1/console/memory", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        content,
        kind: $("#mem-add-kind").value,
        importance: Number($("#mem-add-importance").value),
      }),
    });
    $("#mem-add-content").value = "";
    $("#mem-add-form").hidden = true;
    toast("Memory saved.");
    loadMemory();
  } catch (e2) {
    toast("Save failed: " + e2.message, true);
  }
});

// ── system ────────────────────────────────────────────────────────
async function loadSystem() {
  // Health
  try {
    const h = await apiJson("/v1/health");
    const box = $("#sys-health");
    box.innerHTML = "";
    const add = (k, v, ok) => {
      const row = el("div", "row");
      row.appendChild(el("span", null, k));
      const val = el("span", "mono");
      val.style.color = ok ? "var(--accent)" : "var(--danger)";
      val.textContent = typeof v === "object" ? JSON.stringify(v) : String(v);
      row.appendChild(val);
      box.appendChild(row);
    };
    add("api", h.api, h.api === "ok");
    add("llm", h.llm?.ok ? "ok" : "down", !!h.llm?.ok);
    add("embedder", h.embedder?.ok ? "ok" : "down", !!h.embedder?.ok);
    setConn(true);
  } catch (e) {
    $("#sys-health").textContent = "unavailable: " + e.message;
  }

  // Connectors + experts
  try {
    const s = await apiJson("/v1/console/status");

    const exBox = $("#sys-experts");
    exBox.innerHTML = "";
    s.experts.forEach((x) => {
      const u = el("div", "unit");
      const head = el("div", "unit-head");
      head.appendChild(el("span", "unit-name", x.title));
      const pill = el("span", "badge-pill " + (x.available ? "pill-on" : "pill-off"),
        x.available ? "available" : "needs " + x.missing_connectors.join(", "));
      head.appendChild(pill);
      u.appendChild(head);
      u.appendChild(el("div", "unit-desc", x.expertise));
      exBox.appendChild(u);
    });

    const coBox = $("#sys-connectors");
    coBox.innerHTML = "";
    s.connectors.forEach((c) => {
      const u = el("div", "unit");
      const head = el("div", "unit-head");
      head.appendChild(el("span", "unit-name", c.name));
      head.appendChild(el("span", "badge-pill pill-on", "v" + c.version));
      u.appendChild(head);
      if (c.description) u.appendChild(el("div", "unit-desc", c.description));
      const tools = el("div", "tools-line");
      c.tools.forEach((t) => {
        const span = el("span");
        const cls = t.executor === "server" ? "pill-srv" : "pill-cli";
        span.innerHTML = `${c.name}.${t.name} `;
        const ex = el("span", "badge-pill " + cls, t.executor);
        span.appendChild(ex);
        if (t.requires_approval) span.appendChild(el("span", "badge-pill pill-appr", "approval"));
        tools.appendChild(span);
        tools.appendChild(document.createTextNode("  "));
      });
      u.appendChild(tools);
      coBox.appendChild(u);
    });
  } catch (e) {
    $("#sys-experts").textContent = "unavailable: " + e.message;
  }
}

$("#sys-refresh").addEventListener("click", loadSystem);

// ── settings modal ────────────────────────────────────────────────
function openModal(status = "") {
  $("#key-input").value = getKey();
  $("#key-status").textContent = status;
  $("#modal").hidden = false;
}
$("#settings-btn").addEventListener("click", () => openModal());
$("#key-close").addEventListener("click", () => ($("#modal").hidden = true));
$("#key-save").addEventListener("click", async () => {
  setKey($("#key-input").value.trim());
  $("#modal").hidden = true;
  toast("Key saved.");
  await loadIdentity();
  loaded.system = false; // force re-fetch with new key next visit
});
$("#key-clear").addEventListener("click", () => {
  setKey("");
  $("#key-input").value = "";
  toast("Key cleared.");
});

// ── boot ──────────────────────────────────────────────────────────
loadIdentity();
chatInput.focus();
