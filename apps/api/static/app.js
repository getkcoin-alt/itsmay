/* Vault Zeta console — vanilla SPA.
 * Same-origin API, auth via Bearer token in localStorage.
 * SSE uses fetch + ReadableStream so we can set Authorization
 * (EventSource can't). Markdown is rendered after streaming completes.
 *
 * Designed & Developed by Karnveer Singh — https://www.karnveer.com
 * © 2026 Karnveer Singh. */

const KEY_NAME     = "vault_api_key";
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
  const cls   = "dot " + (ok === null ? "dot-idle" : ok ? "dot-ok" : "dot-bad");
  const title = ok === null ? "connecting…" : ok ? "connected" : "connection error";
  ["#conn-dot", "#conn-dot-m"].forEach((id) => {
    const dot = $(id);
    if (dot) { dot.className = cls; dot.title = title; }
  });
  const label = $("#conn-label");
  if (label) label.textContent = title;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), 3200);
}

// ── markdown renderer ─────────────────────────────────────────────
function escHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inlineMd(s) {
  return escHtml(s)
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(raw) {
  const lines = raw.split("\n");
  const out = [];
  let inCode = false, codeBuf = [];
  let inList = false, listOrdered = false, listBuf = [];

  const flushList = () => {
    if (!listBuf.length) return;
    const tag = listOrdered ? "ol" : "ul";
    out.push(`<${tag}>${listBuf.map((l) => `<li>${l}</li>`).join("")}</${tag}>`);
    listBuf = [];
    inList = false;
  };

  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        out.push(
          `<div class="code-block-wrap"><pre><code>${escHtml(codeBuf.join("\n"))}</code></pre>` +
          `<button class="copy-btn">Copy</button></div>`
        );
        codeBuf = []; inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    if (/^---+$/.test(line.trim())) { flushList(); out.push("<hr>"); continue; }

    const bulletMatch = line.match(/^[-*] (.+)/);
    if (bulletMatch) {
      if (inList && listOrdered) flushList();
      inList = true; listOrdered = false;
      listBuf.push(inlineMd(bulletMatch[1]));
      continue;
    }
    const ordMatch = line.match(/^\d+\. (.+)/);
    if (ordMatch) {
      if (inList && !listOrdered) flushList();
      inList = true; listOrdered = true;
      listBuf.push(inlineMd(ordMatch[1]));
      continue;
    }

    if (!line.trim()) { flushList(); continue; }
    flushList();

    if (line.startsWith("### ")) { out.push(`<h4>${inlineMd(line.slice(4))}</h4>`); continue; }
    if (line.startsWith("## "))  { out.push(`<h3>${inlineMd(line.slice(3))}</h3>`); continue; }
    if (line.startsWith("# "))   { out.push(`<h2>${inlineMd(line.slice(2))}</h2>`); continue; }

    out.push(`<p>${inlineMd(line)}</p>`);
  }

  flushList();
  if (codeBuf.length) {
    out.push(
      `<div class="code-block-wrap"><pre><code>${escHtml(codeBuf.join("\n"))}</code></pre>` +
      `<button class="copy-btn">Copy</button></div>`
    );
  }
  return out.join("");
}

// Delegated copy-button handler wired once per bubble.
function wireCopyButtons(container) {
  container.addEventListener("click", (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;
    const code = btn.closest(".code-block-wrap")?.querySelector("code")?.textContent || "";
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = "Copied!";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1600);
    }).catch(() => {});
  });
}

// ── tabs / navigation ─────────────────────────────────────────────
const loaded = { memory: false, system: false, watch: false, agents: false, mini: false, models: false };

function switchTab(name) {
  document.querySelectorAll(".nav-item[data-tab]").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".mnav-item[data-tab]").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  $(`#panel-${name}`).classList.add("active");

  if (name === "memory" && !loaded.memory) { loadMemory(); loaded.memory = true; }
  if (name === "system" && !loaded.system) { loadSystem(); loaded.system = true; }
  if (name === "mini" && !loaded.mini) { loadMini(); loaded.mini = true; }
  if (name === "models" && !loaded.models) { loadModels(); loaded.models = true; }
  if (name === "watch")  { loadWatch(); }
  if (name === "agents") { clearAgentsTimer(); loadAgents(); }
  if (name !== "agents") { clearAgentsTimer(); }
}

document.querySelectorAll(".nav-item[data-tab], .mnav-item[data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// ── identity ──────────────────────────────────────────────────────
async function loadIdentity() {
  try {
    const d = await apiJson("/v1/console/identity");
    $("#mission-text").textContent = d.mission;
    $("#mission-days").textContent = `${d.days_remaining}d`;
    $("#mission-model").textContent = d.model;
    $("#mission").hidden = false;
    setConn(true);
  } catch (e) {
    if (e.message !== "unauthorized") setConn(false);
  }
}

// ── chat ──────────────────────────────────────────────────────────
const chatLog   = $("#chat-log");
const chatInput = $("#chat-input");
let streaming = false;

function clearEmptyHint() {
  chatLog.querySelector(".empty-hint, .empty-state")?.remove();
}

function addMessage(role, text = "") {
  clearEmptyHint();
  const msg = el("div", `msg ${role}`);

  const avatar = el("div", "msg-avatar");
  avatar.textContent = role === "user" ? "U" : "⚡";
  msg.appendChild(avatar);

  const body = el("div", "msg-body");
  const steps = el("div", "steps");
  body.appendChild(steps);
  const bubble = el("div", "bubble");
  bubble.textContent = text;
  body.appendChild(bubble);
  msg.appendChild(body);

  chatLog.appendChild(msg);
  chatLog.scrollTop = chatLog.scrollHeight;
  return { msg, bubble, steps, body };
}

function addStep(steps, kind, text) {
  steps.appendChild(el("div", `chip chip-${kind}`, text));
  chatLog.scrollTop = chatLog.scrollHeight;
}

function parseSSE(block) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    const cleaned = line.replace(/\r$/, "");
    if (cleaned.trimStart().startsWith(":")) continue;
    if (cleaned.trimStart().startsWith("event:")) {
      event = cleaned.trimStart().slice(6).trim();
    } else if (cleaned.trimStart().startsWith("data:")) {
      dataLines.push(cleaned.trimStart().slice(5).replace(/^ /, ""));
    }
  }
  return { event, data: dataLines.join("\n") };
}

async function sendChat(message) {
  if (streaming) return;
  streaming = true;
  $("#chat-send").disabled = true;
  addMessage("user", message);
  const { bubble, steps, body } = addMessage("assistant", "");
  bubble.classList.add("cursor");

  const sessionId = localStorage.getItem(SESSION_NAME);
  let answer = "";

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

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      
      while (true) {
        let lfIdx = buf.indexOf("\n\n");
        let crlfIdx = buf.indexOf("\r\n\r\n");
        let idx = -1;
        let sepLen = 2;
        
        if (crlfIdx >= 0 && (lfIdx < 0 || crlfIdx < lfIdx)) {
          idx = crlfIdx;
          sepLen = 4;
        } else {
          idx = lfIdx;
          sepLen = 2;
        }
        
        if (idx < 0) break;
        
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + sepLen);
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
          addStep(steps, "call",
            `→ ${d.name}  ${arg.length > 60 ? arg.slice(0, 60) + "…" : arg}`);
        } else if (event === "status") {
          const d = JSON.parse(data);
          addStep(steps, "result", `✓ ${d.tool}: ${String(d.result || "").slice(0, 80)}`);
        } else if (event === "done") {
          const d = JSON.parse(data);
          if (d.tokens_in || d.latency_ms) {
            const meta = el("div", "msg-meta");
            const parts = [];
            if (d.tokens_in)  parts.push(`${d.tokens_in}↑ ${d.tokens_out || 0}↓ tok`);
            if (d.latency_ms) parts.push(`${(d.latency_ms / 1000).toFixed(1)}s`);
            meta.textContent = parts.join(" · ");
            body.appendChild(meta);
          }
        } else if (event === "error") {
          const d = JSON.parse(data);
          addStep(steps, "call", `error: ${d.error}`);
          toast("Stream error: " + d.error, true);
        }
      }
    }

    if (answer) {
      bubble.innerHTML = renderMarkdown(answer);
      bubble.classList.add("rendered");
      wireCopyButtons(bubble);
    } else {
      bubble.textContent = "(no response)";
    }
    setConn(true);
  } catch (e) {
    if (!answer) bubble.textContent = "(failed)";
    if (e.message !== "unauthorized") toast("Chat failed: " + e.message, true);
  } finally {
    bubble.classList.remove("cursor");
    streaming = false;
    $("#chat-send").disabled = false;
    chatLog.scrollTop = chatLog.scrollHeight;
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
  chatLog.appendChild(el("div", "empty-hint muted", "New session started. Ask Scrappy anything."));
  toast("Started a fresh session.");
});

// ── memory ────────────────────────────────────────────────────────
const memList = $("#mem-list");
let memOffset = 0;
let memTotal  = 0;
const MEM_LIMIT = 50;

function memItem(m, { showSim } = {}) {
  const item = el("div", "mem-item");
  item.dataset.id = m.id;

  const body = el("div", "mem-body");
  body.appendChild(el("div", "mem-content", m.content));

  const meta = el("div", "mem-meta-row");
  meta.appendChild(el("span", `mem-kind ${m.kind}`, m.kind));

  const impBar = el("div", "imp-bar");
  const impFill = el("div", "imp-fill");
  impFill.style.width = `${Math.round((m.importance || 0) * 100)}%`;
  impBar.appendChild(impFill);
  meta.appendChild(impBar);
  meta.appendChild(el("span", null, `imp ${(m.importance || 0).toFixed(2)}`));

  if (showSim && m.similarity != null)
    meta.appendChild(el("span", "sim-score", `sim ${m.similarity.toFixed(3)}`));
  if (m.source)     meta.appendChild(el("span", null, m.source));
  if (m.use_count != null) meta.appendChild(el("span", null, `used ${m.use_count}×`));
  if (m.created_at)
    meta.appendChild(el("span", null, new Date(m.created_at).toLocaleDateString()));
  body.appendChild(meta);
  item.appendChild(body);

  const del = el("button", "mem-del", "✕");
  del.title = "delete";
  del.addEventListener("click", async () => {
    if (!confirm("Delete this memory?")) return;
    del.disabled = true;
    try {
      await api(`/v1/console/memory/${m.id}`, { method: "DELETE" });
      item.remove();
      memTotal = Math.max(0, memTotal - 1);
      updateMemMeta();
      toast("Memory deleted.");
    } catch (e) {
      del.disabled = false;
      toast("Delete failed: " + e.message, true);
    }
  });
  item.appendChild(del);
  return item;
}

function updateMemMeta(extra) {
  const shown = memList.querySelectorAll(".mem-item").length;
  $("#mem-meta").textContent = extra || `${memTotal} memories stored · showing ${shown}`;
}

async function loadMemory(opts = {}) {
  const { append = false, offset = 0, searchResult = false } = opts;
  if (!append) {
    memOffset = 0;
    memList.innerHTML = "";
    $("#mem-load-more").hidden = true;
  }
  if (!searchResult) $("#mem-meta").textContent = "Loading…";
  try {
    const d = await apiJson(`/v1/console/memory?limit=${MEM_LIMIT}&offset=${offset}`);
    memTotal = d.total;
    if (!d.memories.length && !append) {
      memList.appendChild(el("div", "muted", "Nothing stored yet."));
      updateMemMeta();
      return;
    }
    d.memories.forEach((m) => memList.appendChild(memItem(m)));
    updateMemMeta();
    memOffset = offset + d.memories.length;
    $("#mem-load-more").hidden = memOffset >= memTotal;
  } catch (e) {
    $("#mem-meta").textContent = "Failed to load: " + e.message;
  }
}

function renderMemSearch(results, query) {
  memList.innerHTML = "";
  if (!results.length) {
    memList.appendChild(el("div", "muted", `No matches for "${query}".`));
    $("#mem-meta").textContent = `0 matches for "${query}"`;
    $("#mem-load-more").hidden = true;
    return;
  }
  results.forEach((m) => memList.appendChild(memItem(m, { showSim: true })));
  $("#mem-meta").textContent = `${results.length} matches for "${query}" (semantic)`;
  $("#mem-load-more").hidden = true;
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
    renderMemSearch(d.results, q);
  } catch (e2) {
    $("#mem-meta").textContent = "Search failed: " + e2.message;
  }
});

$("#mem-refresh").addEventListener("click", () => {
  $("#mem-search-input").value = "";
  loaded.memory = false;
  loadMemory();
  loaded.memory = true;
});

$("#mem-more-btn").addEventListener("click", () =>
  loadMemory({ append: true, offset: memOffset }));

$("#mem-add-toggle").addEventListener("click", () => {
  const form = $("#mem-add-form");
  form.hidden = !form.hidden;
  if (!form.hidden) $("#mem-add-content").focus();
});

$("#mem-add-cancel").addEventListener("click", () => {
  $("#mem-add-form").hidden = true;
  $("#mem-add-content").value = "";
});

$("#mem-add-importance").addEventListener("input", (e) => {
  $("#mem-add-importance-val").textContent = Number(e.target.value).toFixed(2);
});

$("#mem-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = $("#mem-add-content").value.trim();
  if (!content) return;
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    await api("/v1/console/memory", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        content,
        kind:       $("#mem-add-kind").value,
        importance: Number($("#mem-add-importance").value),
      }),
    });
    $("#mem-add-content").value = "";
    $("#mem-add-form").hidden = true;
    toast("Memory saved.");
    loaded.memory = false;
    loadMemory();
    loaded.memory = true;
  } catch (e2) {
    toast("Save failed: " + e2.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "Save memory";
  }
});

// ── watch tab ─────────────────────────────────────────────────────
async function loadWatch() {
  const box = $("#watch-events");
  box.innerHTML = "";
  box.appendChild(el("div", "empty-feed-msg", "Loading…"));
  try {
    const d = await apiJson("/v1/browser/watch-status");
    box.innerHTML = "";
    if (!d.events || !d.events.length) {
      box.appendChild(el("div", "empty-feed-msg",
        "No tab events yet. Start watching a tab using the form above."));
      return;
    }
    d.events.forEach((ev) => box.appendChild(watchEventEl(ev)));
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", "empty-feed-msg",
      "Could not load watch events: " + e.message));
  }
}

function watchEventEl(ev) {
  const wrap = el("div", "watch-event");
  const head = el("div", "watch-event-head");
  const left = el("div");
  left.appendChild(el("div", "watch-tab-name", ev.title || ev.url || "Unknown tab"));
  if (ev.url) left.appendChild(el("div", "watch-tab-url", ev.url));
  head.appendChild(left);
  if (ev.received_at)
    head.appendChild(el("div", "watch-event-time", timeAgo(new Date(ev.received_at))));
  wrap.appendChild(head);
  wrap.appendChild(el("div", "watch-event-body", ev.summary || "(no summary)"));
  return wrap;
}

function timeAgo(date) {
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs < 60)    return `${secs}s ago`;
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return date.toLocaleDateString();
}

$("#watch-refresh").addEventListener("click", loadWatch);

$("#watch-send-btn").addEventListener("click", () => {
  const msg = $("#watch-prompt-input").value.trim();
  if (!msg) { toast("Type a message first.", true); return; }
  $("#watch-prompt-input").value = "";
  switchTab("chat");
  sendChat(msg);
});
$("#watch-prompt-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#watch-send-btn").click(); }
});

// ── agents tab ────────────────────────────────────────────────────
let _agentsTimer = null;
const _expandedAgents = new Set();

function clearAgentsTimer() {
  if (_agentsTimer) { clearTimeout(_agentsTimer); _agentsTimer = null; }
}

function agentDuration(a) {
  const start = new Date(a.created_at);
  const end   = a.finished_at ? new Date(a.finished_at) : new Date();
  const secs  = Math.floor((end - start) / 1000);
  if (secs < 60)   return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

function updateAgentsBadge(agents) {
  const active = agents.filter((a) => a.status === "pending" || a.status === "running").length;
  const badge = $("#agents-badge");
  if (!badge) return;
  badge.textContent = active;
  badge.hidden = active === 0;
}

async function loadAgents() {
  const list = $("#agents-list");
  try {
    const agents = await apiJson("/v1/agents");
    updateAgentsBadge(agents);
    list.innerHTML = "";
    if (!agents.length) {
      const empty = el("div", "empty-state sm");
      empty.innerHTML =
        '<div class="empty-icon">⬡</div>' +
        '<div class="empty-title">No agents yet</div>' +
        '<div class="empty-body">Ask Scrappy to complete a task that needs code execution or file work — he\'ll spawn workers automatically.</div>';
      list.appendChild(empty);
      return;
    }
    agents.forEach((a) => list.appendChild(buildAgentCard(a)));
    const hasActive = agents.some((a) => a.status === "pending" || a.status === "running");
    if (hasActive) _agentsTimer = setTimeout(loadAgents, 3000);
  } catch (e) {
    list.innerHTML = "";
    list.appendChild(el("div", "muted", "Failed to load: " + e.message));
  }
}

function buildAgentCard(a) {
  const isActive = a.status === "pending" || a.status === "running";
  const isExpanded = _expandedAgents.has(a.id);

  let cls = "agent-card";
  if (a.status === "running") cls += " running";
  if (isExpanded) cls += " expanded";
  const card = el("div", cls);
  card.dataset.id = a.id;

  const head = el("div", "agent-card-head");
  head.appendChild(el("span", "agent-expand-icon", "▶"));
  head.appendChild(el("span", `status-pill sp-${a.status}`, a.status));
  head.appendChild(el("span", "agent-id-badge", a.id));
  head.appendChild(el("span", "agent-task-text", a.task));
  head.appendChild(el("span", "agent-duration", agentDuration(a)));

  if (isActive) {
    const cancelBtn = el("button", "agent-cancel-btn", "Cancel");
    cancelBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      cancelBtn.disabled = true; cancelBtn.textContent = "…";
      try {
        await api(`/v1/agents/${a.id}`, { method: "DELETE" });
        toast("Agent cancelled.");
        clearAgentsTimer();
        loadAgents();
      } catch (err) {
        toast("Cancel failed: " + err.message, true);
        cancelBtn.disabled = false; cancelBtn.textContent = "Cancel";
      }
    });
    head.appendChild(cancelBtn);
  }

  card.appendChild(head);

  if (isExpanded) fetchAndRenderDetail(card, a.id);

  head.addEventListener("click", () => {
    if (_expandedAgents.has(a.id)) {
      _expandedAgents.delete(a.id);
      card.classList.remove("expanded");
      while (card.children.length > 1) card.removeChild(card.lastChild);
    } else {
      _expandedAgents.add(a.id);
      card.classList.add("expanded");
      fetchAndRenderDetail(card, a.id);
    }
  });

  return card;
}

async function fetchAndRenderDetail(card, agentId) {
  while (card.children.length > 1) card.removeChild(card.lastChild);
  const logWrap = el("div", "agent-log");
  const inner = el("div", "agent-log-inner");
  inner.appendChild(el("div", "muted", "Loading…"));
  logWrap.appendChild(inner);
  card.appendChild(logWrap);

  try {
    const a = await apiJson(`/v1/agents/${agentId}`);
    while (card.children.length > 1) card.removeChild(card.lastChild);
    renderAgentDetail(card, a);

    if ((a.status === "pending" || a.status === "running") && _expandedAgents.has(agentId)) {
      setTimeout(() => {
        if (_expandedAgents.has(agentId)) fetchAndRenderDetail(card, agentId);
      }, 2000);
    }
  } catch (e) {
    while (card.children.length > 1) card.removeChild(card.lastChild);
    const errWrap = el("div", "agent-log");
    errWrap.appendChild(el("div", "muted", "Error: " + e.message));
    card.appendChild(errWrap);
  }
}

function renderAgentDetail(card, a) {
  if (a.log && a.log.length) {
    const logWrap = el("div", "agent-log");
    const inner = el("div", "agent-log-inner");
    a.log.forEach((entry) => {
      const row = el("div", "log-entry");
      const gutter = el("div", "log-gutter");
      gutter.appendChild(el("span", `log-kind-label lk-${entry.kind}`, entry.kind));
      row.appendChild(gutter);
      const txt = el("div", `log-text ${entry.kind}`);
      txt.textContent = entry.text;
      row.appendChild(txt);
      inner.appendChild(row);
    });
    logWrap.appendChild(inner);
    card.appendChild(logWrap);
    logWrap.scrollTop = logWrap.scrollHeight;
  }
  if (a.result && (a.status === "done" || a.status === "error")) {
    const bar = el("div", "agent-result-bar");
    bar.textContent = a.result;
    card.appendChild(bar);
  }
}

$("#agents-refresh").addEventListener("click", () => {
  clearAgentsTimer();
  loadAgents();
});

// ── system ────────────────────────────────────────────────────────
async function loadSystem() {
  try {
    const h = await apiJson("/v1/health");
    const box = $("#sys-health");
    box.innerHTML = "";

    const addRow = (k, v, ok) => {
      const row = el("div", "kv-row");
      row.appendChild(el("span", "kv-key", k));
      const val = el("span", `kv-val ${ok === null ? "dim" : ok ? "ok" : "bad"}`);
      val.textContent = String(v);
      row.appendChild(val);
      box.appendChild(row);
    };

    addRow("api", h.api, h.api === "ok");
    if (h.llm) {
      addRow("llm", h.llm.ok ? "ok" : "down", !!h.llm.ok);
      if (h.llm.model)    addRow("model", h.llm.model, null);
      if (h.llm.provider) addRow("provider", h.llm.provider, null);
    }
    if (h.embedder) {
      addRow("embedder", h.embedder.ok ? "ok" : "down", !!h.embedder.ok);
      if (h.embedder.model)    addRow("model", h.embedder.model, null);
      if (h.embedder.provider) addRow("provider", h.embedder.provider, null);
      if (h.embedder.dim)      addRow("dim", h.embedder.dim, null);
    }
    setConn(true);
  } catch (e) {
    $("#sys-health").textContent = "unavailable: " + e.message;
  }

  try {
    const s = await apiJson("/v1/console/status");

    const exBox = $("#sys-experts");
    exBox.innerHTML = "";
    s.experts.forEach((x) => {
      const u = el("div", "unit-card");
      const head = el("div", "unit-head");
      head.appendChild(el("span", "unit-name", x.title));
      head.appendChild(el("span",
        "pill " + (x.available ? "pill-on" : "pill-off"),
        x.available ? "available" : "needs: " + x.missing_connectors.join(", ")));
      u.appendChild(head);
      if (x.expertise) u.appendChild(el("div", "unit-desc", x.expertise));
      exBox.appendChild(u);
    });

    const coBox = $("#sys-connectors");
    coBox.innerHTML = "";
    s.connectors.forEach((c) => {
      const u = el("div", "unit-card");
      const head = el("div", "unit-head");
      head.appendChild(el("span", "unit-name", c.name));
      head.appendChild(el("span", "pill pill-on", "v" + c.version));
      u.appendChild(head);
      if (c.description) u.appendChild(el("div", "unit-desc", c.description));

      const tools = el("div", "tools-line");
      c.tools.forEach((t) => {
        const span = el("span");
        span.innerHTML = `${c.name}.${t.name} `;
        span.appendChild(el("span", "pill " + (t.executor === "server" ? "pill-srv" : "pill-cli"), t.executor));
        if (t.requires_approval)
          span.appendChild(el("span", "pill pill-appr", "approval"));
        tools.appendChild(span);
      });
      u.appendChild(tools);
      coBox.appendChild(u);
    });
  } catch (e) {
    $("#sys-experts").textContent = "unavailable: " + e.message;
  }
}

$("#sys-refresh").addEventListener("click", () => {
  loaded.system = false;
  loadSystem();
  loaded.system = true;
});

// ── settings modal ────────────────────────────────────────────────
function openModal(status = "") {
  $("#key-input").value = getKey();
  $("#key-status").textContent = status;
  $("#modal").hidden = false;
  $("#key-input").focus();
}

["#settings-btn", "#settings-btn-m"].forEach((id) => {
  const btn = $(id);
  if (btn) btn.addEventListener("click", () => openModal());
});

$("#key-close").addEventListener("click", () => ($("#modal").hidden = true));
$("#modal").addEventListener("click", (e) => {
  if (e.target === $("#modal")) $("#modal").hidden = true;
});

$("#key-save").addEventListener("click", async () => {
  setKey($("#key-input").value.trim());
  $("#modal").hidden = true;
  toast("Key saved.");
  await loadIdentity();
  loaded.system = false;
  loaded.memory = false;
  const activeTab =
    document.querySelector(".nav-item[data-tab].active, .mnav-item[data-tab].active")
      ?.dataset.tab;
  if (activeTab === "system") { loadSystem(); loaded.system = true; }
  if (activeTab === "memory") { loadMemory(); loaded.memory = true; }
});

$("#key-clear").addEventListener("click", () => {
  setKey("");
  $("#key-input").value = "";
  toast("Key cleared.");
  setConn(false);
});

// ── boot ──────────────────────────────────────────────────────────
loadIdentity();
chatInput.focus();


// ── Mini AI Voice Interface ────────────────────────────────────────

const miniVoiceStatus = $("#mini-voice-status");
const miniVoiceSubtitle = $("#mini-voice-subtitle");
const miniAvatarContainer = $("#mini-avatar-container");
const miniPttBtn = $("#mini-ptt-btn");
let currentPersona = "friend";

// Audio Queue Manager for sequential playback
// Shared audio element to bypass autoplay restrictions
const sharedAudio = new Audio();

// Audio Queue Manager for sequential playback
class AudioQueue {
  constructor() {
    this.queue = [];
    this.isPlaying = false;
  }

  push(sentence) {
    if (!sentence.trim()) return;
    this.queue.push(sentence);
    this.playNext();
  }

  async playNext() {
    if (this.isPlaying || this.queue.length === 0) return;
    this.isPlaying = true;
    const text = this.queue.shift();
    
    // Show what is being spoken
    miniVoiceSubtitle.textContent = text;
    miniVoiceSubtitle.hidden = false;
    miniAvatarContainer.classList.add("speaking");
    miniVoiceStatus.textContent = "Mini is speaking...";

    try {
      const token = getKey();
      const params = new URLSearchParams({ text });
      if (token) params.set("_token", token);
      
      sharedAudio.src = `/v1/voice/speak?${params.toString()}`;
      
      sharedAudio.onended = () => {
        miniAvatarContainer.classList.remove("speaking");
        this.isPlaying = false;
        if (this.queue.length === 0) {
          miniVoiceStatus.textContent = "Hold spacebar or button to talk";
          miniVoiceSubtitle.hidden = true;
        } else {
          this.playNext();
        }
      };
      
      sharedAudio.onerror = () => {
        console.error("Audio playback error");
        miniAvatarContainer.classList.remove("speaking");
        this.isPlaying = false;
        this.playNext();
      };

      await sharedAudio.play();
    } catch (e) {
      console.error("TTS fetch failed", e);
      miniAvatarContainer.classList.remove("speaking");
      this.isPlaying = false;
      this.playNext();
    }
  }

  stop() {
    this.queue = [];
    sharedAudio.pause();
    this.isPlaying = false;
    miniAvatarContainer.classList.remove("speaking");
    miniVoiceStatus.textContent = "Hold spacebar or button to talk";
    miniVoiceSubtitle.hidden = true;
  }
}

const ttsQueue = new AudioQueue();

async function loadMini() {
  try {
    const p = await apiJson("/v1/mini/profile");
    currentPersona = p.persona;
    updateMiniPersonaUI(p.persona, p.persona_title);
  } catch (e) {
    console.error("Failed to load Mini profile:", e);
  }
}

function updateMiniPersonaUI(persona, title) {
  currentPersona = persona;
  $("#mini-persona-badge").textContent = title;
  
  document.querySelectorAll(".persona-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.persona === persona);
  });

  const panel = $("#panel-mini");
  panel.className = "panel active " + (persona === "mentor" ? "mentor-theme" : "");
}

document.querySelectorAll(".persona-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const persona = btn.dataset.persona;
    if (persona === currentPersona) return;
    try {
      const p = await apiJson("/v1/mini/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona }),
      });
      updateMiniPersonaUI(p.persona, p.persona_title);
      toast(`Switched to ${p.persona_title} persona.`);
    } catch (e) {
      toast("Failed to switch persona: " + e.message, true);
    }
  });
});

// Microphone and Voice Loop
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

async function startRecording() {
  if (isRecording) return;
  
  // Unlock audio playback on first interaction
  sharedAudio.play().catch(() => {});
  
  // Stop current playback if interrupting
  ttsQueue.stop();
  
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunks.push(event.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' }); // Chrome often uses webm for audio
      await handleAudioSubmission(audioBlob);
    };

    mediaRecorder.start();
    isRecording = true;
    miniPttBtn.classList.add("active");
    miniVoiceStatus.textContent = "Listening...";
    miniVoiceSubtitle.hidden = true;
  } catch (e) {
    console.error("Microphone access denied or error:", e);
    toast("Microphone access denied. Please allow microphone permissions.", true);
  }
}

function stopRecording() {
  if (!isRecording || !mediaRecorder) return;
  isRecording = false;
  miniPttBtn.classList.remove("active");
  mediaRecorder.stop();
  miniVoiceStatus.textContent = "Processing...";
}

// Push to talk event listeners
miniPttBtn.addEventListener("mousedown", startRecording);
miniPttBtn.addEventListener("mouseup", stopRecording);
miniPttBtn.addEventListener("mouseleave", stopRecording);
miniPttBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
miniPttBtn.addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });
miniPttBtn.addEventListener("touchcancel", (e) => { e.preventDefault(); stopRecording(); });

// Also allow spacebar when Mini panel is active
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && $("#panel-mini").classList.contains("active") && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault();
    startRecording();
  }
});

document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && $("#panel-mini").classList.contains("active") && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault();
    stopRecording();
  }
});

async function handleAudioSubmission(blob) {
  miniVoiceStatus.textContent = "Thinking...";
  
  try {
    // 1. Transcribe
    const formData = new FormData();
    // Use .webm or .wav depending on what the browser generated. Whisper handles both.
    formData.append("audio", blob, "voice.webm");
    
    const token = getKey();
    const sttRes = await fetch("/v1/voice/transcribe", {
      method: "POST",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: formData
    });
    
    if (!sttRes.ok) {
      const errText = await sttRes.text().catch(() => "");
      throw new Error(`STT failed (${sttRes.status}): ${errText}`);
    }
    const sttData = await sttRes.json();
    const transcript = sttData.text.trim();
    
    if (!transcript) {
      miniVoiceStatus.textContent = "Hold spacebar or button to talk";
      return;
    }

    await sendToMiniAI(transcript);
  } catch (e) {
    console.error("Voice loop error:", e);
    toast("Voice error: " + e.message, true);
    miniVoiceStatus.textContent = "Hold spacebar or button to talk";
  }
}

async function sendToMiniAI(transcript) {
  miniVoiceSubtitle.textContent = `You: "${transcript}"`;
  miniVoiceSubtitle.hidden = false;
  miniVoiceStatus.textContent = "Thinking...";
  
  try {
    // Stop any ongoing audio before starting new conversation
    ttsQueue.stop();
    
    // 2. Send to Mini Chat LLM via SSE
    const chatRes = await api("/v1/mini/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: transcript, persona: currentPersona }),
    });

    const reader = chatRes.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sentenceBuffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      while (true) {
        let lfIdx = buf.indexOf("\n\n");
        let crlfIdx = buf.indexOf("\r\n\r\n");
        let idx = -1;
        let sepLen = 2;

        if (crlfIdx >= 0 && (lfIdx < 0 || crlfIdx < lfIdx)) {
          idx = crlfIdx;
          sepLen = 4;
        } else {
          idx = lfIdx;
          sepLen = 2;
        }

        if (idx < 0) break;

        const block = buf.slice(0, idx);
        buf = buf.slice(idx + sepLen);
        const { event, data } = parseSSE(block);

        if (event === "token") {
          sentenceBuffer += data;
          // Simple sentence boundary detection
          if (/[.!?]\s/.test(sentenceBuffer) || /[.!?]$/.test(data)) {
             const sentence = sentenceBuffer.trim();
             if (sentence) {
               ttsQueue.push(sentence);
             }
             sentenceBuffer = "";
          }
        }
      }
    }
    
    // Push any remaining text
    if (sentenceBuffer.trim()) {
      ttsQueue.push(sentenceBuffer.trim());
    }
    
  } catch (e) {
    console.error("Mini Chat error:", e);
    toast("Mini AI error: " + e.message, true);
    miniVoiceStatus.textContent = "Hold spacebar or button to talk";
  }
}

// Text fallback submission for Mini AI
$("#mini-text-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#mini-text-input");
  const text = input.value.trim();
  if (!text) return;
  
  input.value = "";
  
  // Unlock audio context on form submit
  sharedAudio.play().catch(() => {});
  
  await sendToMiniAI(text);
});

// Shift+Enter for newline in Mini Text input
$("#mini-text-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#mini-text-form").dispatchEvent(new Event("submit"));
  }
});
// Memories panel (keep as before)
$("#mini-memories-btn").addEventListener("click", async () => {
  const panel = $("#mini-memories-panel");
  const list = $("#mini-memories-list");
  panel.hidden = false;
  list.innerHTML = `<div class="mini-memories-empty">Loading memories…</div>`;
  
  try {
    const data = await apiJson("/v1/mini/memories");
    list.innerHTML = "";
    if (data.memories && data.memories.length > 0) {
      data.memories.forEach((m) => {
        const card = el("div", "mini-memory-card");
        card.textContent = m.content;
        list.appendChild(card);
      });
    } else {
      list.innerHTML = `<div class="mini-memories-empty">No memories yet — start chatting and I'll remember the important stuff.</div>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="mini-memories-empty bad">Failed to load: ${e.message}</div>`;
  }
});

$("#mini-memories-close").addEventListener("click", () => {
  $("#mini-memories-panel").hidden = true;
});



// ── Model Settings ────────────────────────────────────────────────
async function loadModels() {
  const scrappyInfo = $("#models-scrappy-info");
  const miniInfo = $("#models-mini-info");
  const ollamaStatus = $("#ollama-status");
  const ollamaList = $("#ollama-models-list");

  scrappyInfo.textContent = "Loading…";
  miniInfo.textContent = "Loading…";
  ollamaStatus.textContent = "Loading…";
  ollamaList.innerHTML = "";

  try {
    // 1. Get current configs
    const data = await apiJson("/v1/settings/models");
    
    scrappyInfo.textContent = `${data.scrappy.provider.toUpperCase()} (${data.scrappy.model})`;
    miniInfo.textContent = `${data.mini.provider.toUpperCase()} (${data.mini.model})`;

    // Highlight active provider card
    document.querySelectorAll(".provider-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.provider === data.scrappy.provider);
      // Pre-fill model name
      if (card.dataset.provider === data.scrappy.provider) {
        card.querySelector("input[type='text']").value = data.scrappy.model;
      }
    });

    // 2. Get local Ollama models
    const local = await apiJson("/v1/settings/ollama/models");
    if (local.available) {
      ollamaStatus.textContent = `Ollama is running at ${data.ollama.host}`;
      ollamaList.innerHTML = "";
      if (local.models && local.models.length > 0) {
        local.models.forEach((m) => {
          const card = el("div", "ollama-model-card");
          
          const name = el("div", "ollama-model-name", m.name);
          card.appendChild(name);
          
          const meta = el("div", "ollama-model-meta");
          meta.appendChild(el("span", "", m.size_human));
          if (m.parameter_size) meta.appendChild(el("span", "dim", m.parameter_size));
          if (m.quantization) meta.appendChild(el("span", "dim", m.quantization));
          card.appendChild(meta);

          const actions = el("div", "ollama-model-actions");
          
          const btnUseChat = el("button", "sm", "Use for Chat");
          btnUseChat.addEventListener("click", () => useOllamaModel(m.name, "chat"));
          actions.appendChild(btnUseChat);
          
          const btnUseMini = el("button", "sm", "Use for Mini");
          btnUseMini.addEventListener("click", () => useOllamaModel(m.name, "mini"));
          actions.appendChild(btnUseMini);
          
          card.appendChild(actions);
          ollamaList.appendChild(card);
        });
      } else {
        ollamaList.innerHTML = `<div class="ollama-status">No models installed. Run "ollama pull <model>" in terminal.</div>`;
      }
    } else {
      ollamaStatus.textContent = `Ollama is unreachable (${local.error || "offline"})`;
      ollamaList.innerHTML = `<div class="ollama-status dim">Ollama is offline. Start the Ollama app to use local models.</div>`;
    }
  } catch (e) {
    toast("Failed to load model settings: " + e.message, true);
  }
}

// Test Provider
async function testProvider(provider) {
  const card = document.querySelector(`.provider-card[data-provider='${provider}']`);
  const keyInput = card.querySelector("input[type='password']");
  const modelInput = card.querySelector("input[type='text']");
  const statusLabel = card.querySelector(".provider-status");

  const key = keyInput.value.trim();
  const model = modelInput.value.trim();
  
  let base_url = "";
  if (provider === "groq") base_url = "https://api.groq.com/openai/v1";
  if (provider === "openrouter") base_url = "https://openrouter.ai/api/v1";
  if (provider === "openai") base_url = "https://api.openai.com/v1";

  statusLabel.textContent = "testing…";
  
  try {
    const res = await apiJson("/v1/settings/test-provider", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "openai", base_url, api_key: key, model }),
    });

    if (res.ok) {
      statusLabel.textContent = "success (200)";
      toast(`Connection to ${provider.toUpperCase()} verified!`);
    } else {
      statusLabel.textContent = `error (${res.status || "fail"})`;
      toast(`Connection test failed: ${res.error}`, true);
    }
  } catch (e) {
    statusLabel.textContent = "error";
    toast("Test failed: " + e.message, true);
  }
}

// Activate Provider
async function activateProvider(provider) {
  const card = document.querySelector(`.provider-card[data-provider='${provider}']`);
  const keyInput = card.querySelector("input[type='password']");
  const modelInput = card.querySelector("input[type='text']");

  const key = keyInput.value.trim();
  const model = modelInput.value.trim();
  
  if (!model) {
    toast("Please specify a model name first.", true);
    return;
  }

  let base_url = "";
  if (provider === "groq") base_url = "https://api.groq.com/openai/v1";
  if (provider === "openrouter") base_url = "https://openrouter.ai/api/v1";
  if (provider === "openai") base_url = "https://api.openai.com/v1";

  try {
    const res = await apiJson("/v1/settings/models", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: "openai",
        base_url,
        api_key: key || undefined,
        model,
      }),
    });

    toast("Configuration saved! Restart server to apply.");
    loadModels();
  } catch (e) {
    toast("Failed to save: " + e.message, true);
  }
}

// Use local Ollama model
async function useOllamaModel(modelName, target) {
  try {
    if (target === "chat") {
      await apiJson("/v1/settings/models", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "ollama",
          model: modelName,
        }),
      });
      toast("Scrappy chat switched to Ollama model! Restart server to apply.");
    } else {
      // For Mini AI companion
      await apiJson("/v1/settings/models", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          companion_provider: "ollama",
          companion_model: modelName,
        }),
      });
      toast("Mini AI companion switched to local Ollama model! Restart server to apply.");
    }
    loadModels();
  } catch (e) {
    toast("Failed to switch model: " + e.message, true);
  }
}

// Make functions globally accessible for HTML onclick handlers
window.testProvider = testProvider;
window.activateProvider = activateProvider;

$("#models-refresh").addEventListener("click", () => {
  loadModels();
});

