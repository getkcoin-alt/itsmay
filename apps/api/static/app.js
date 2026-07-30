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
const loaded = { memory: false, system: false, watch: false, agents: false };

function switchTab(name) {
  document.querySelectorAll(".nav-item[data-tab]").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".mnav-item[data-tab]").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  $(`#panel-${name}`).classList.add("active");

  if (name === "memory" && !loaded.memory) { loadMemory(); loaded.memory = true; }
  if (name === "system" && !loaded.system) { loadSystem(); loaded.system = true; }
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
    if (line.startsWith(":")) continue;
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
