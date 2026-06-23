/* Vault Zeta console — vanilla SPA.
 * Same-origin API, auth via Bearer token in localStorage.
 * SSE uses fetch + ReadableStream so we can set Authorization
 * (EventSource can't). Markdown is rendered after streaming completes. */

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
  const dot = $("#conn-dot");
  dot.className = "dot " + (ok === null ? "dot-idle" : ok ? "dot-ok" : "dot-bad");
  dot.title = ok === null ? "connecting…" : ok ? "connected" : "connection error";
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
    // fenced code block
    if (line.startsWith("```")) {
      if (inCode) {
        out.push(`<pre><code>${escHtml(codeBuf.join("\n"))}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    // horizontal rule
    if (/^---+$/.test(line.trim())) { flushList(); out.push("<hr>"); continue; }

    // bullet list
    const bulletMatch = line.match(/^[-*] (.+)/);
    if (bulletMatch) {
      if (inList && listOrdered) flushList();
      inList = true; listOrdered = false;
      listBuf.push(inlineMd(bulletMatch[1]));
      continue;
    }
    // ordered list
    const ordMatch = line.match(/^\d+\. (.+)/);
    if (ordMatch) {
      if (inList && !listOrdered) flushList();
      inList = true; listOrdered = true;
      listBuf.push(inlineMd(ordMatch[1]));
      continue;
    }

    // blank line — flush list, paragraph break
    if (!line.trim()) { flushList(); continue; }

    flushList();

    // headings
    if (line.startsWith("### ")) { out.push(`<h4>${inlineMd(line.slice(4))}</h4>`); continue; }
    if (line.startsWith("## "))  { out.push(`<h3>${inlineMd(line.slice(3))}</h3>`); continue; }
    if (line.startsWith("# "))   { out.push(`<h2>${inlineMd(line.slice(2))}</h2>`); continue; }

    out.push(`<p>${inlineMd(line)}</p>`);
  }

  flushList();
  if (codeBuf.length) out.push(`<pre><code>${escHtml(codeBuf.join("\n"))}</code></pre>`);

  return out.join("");
}

// ── tabs ──────────────────────────────────────────────────────────
const loaded = { memory: false, system: false, watch: false };
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    const name = tab.dataset.tab;
    $(`#panel-${name}`).classList.add("active");
    if (name === "memory" && !loaded.memory) { loadMemory(); loaded.memory = true; }
    if (name === "system" && !loaded.system) { loadSystem(); loaded.system = true; }
    if (name === "watch")                    { loadWatch(); }
  });
});

// ── identity header ───────────────────────────────────────────────
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

// Parse one SSE block (event: x\ndata: y) → {event, data}.
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
  const { msg, bubble, steps } = addMessage("assistant", "");
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
          const res = String(d.result || "").slice(0, 80);
          addStep(steps, "result", `✓ ${d.tool}: ${res}`);
        } else if (event === "done") {
          const d = JSON.parse(data);
          if (d.tokens_in || d.latency_ms) {
            const stat = el("div", "msg-stats");
            const parts = [];
            if (d.tokens_in)   parts.push(`${d.tokens_in}in / ${d.tokens_out || 0}out tokens`);
            if (d.latency_ms)  parts.push(`${(d.latency_ms / 1000).toFixed(1)}s`);
            stat.textContent = parts.join(" · ");
            msg.appendChild(stat);
          }
        } else if (event === "error") {
          const d = JSON.parse(data);
          addStep(steps, "call", `error: ${d.error}`);
          toast("Stream error: " + d.error, true);
        }
      }
    }

    // Render markdown now that streaming is complete.
    if (answer) {
      bubble.innerHTML = renderMarkdown(answer);
      bubble.classList.add("rendered");
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
  chatLog.appendChild(el("div", "empty-hint", "New session started. Ask Scrappy anything."));
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
  const body = el("div", "body");
  body.appendChild(el("div", "content", m.content));
  const meta = el("div", "meta");
  meta.appendChild(el("span", "badge", m.kind));
  meta.appendChild(el("span", null, `importance ${m.importance}`));
  if (showSim && m.similarity != null)
    meta.appendChild(el("span", "sim", `sim ${m.similarity}`));
  if (m.source)    meta.appendChild(el("span", null, m.source));
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
  $("#mem-meta").textContent = extra ||
    `${memTotal} memories stored · showing ${shown}`;
}

async function loadMemory(opts = {}) {
  const { append = false, offset = 0, searchResult = false } = opts;
  if (!append) {
    memOffset = 0;
    memList.innerHTML = "";
    $("#mem-load-more").hidden = true;
  }
  if (!searchResult) {
    $("#mem-meta").textContent = "Loading…";
  }
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
    const hasMore = memOffset < memTotal;
    $("#mem-load-more").hidden = !hasMore;
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
  btn.disabled = true;
  btn.textContent = "Saving…";
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
    btn.disabled = false;
    btn.textContent = "Save";
  }
});

// ── watch tab ─────────────────────────────────────────────────────
async function loadWatch() {
  const box = $("#watch-events");
  box.innerHTML = "";
  box.appendChild(el("div", "muted watch-empty", "Loading…"));
  try {
    const d = await apiJson("/v1/browser/watch-status");
    box.innerHTML = "";
    if (!d.events || !d.events.length) {
      box.appendChild(el("div", "muted watch-empty",
        "No tab events yet. Start watching a tab using the form above."));
      return;
    }
    d.events.forEach((ev) => box.appendChild(watchEventEl(ev)));
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", "muted watch-empty",
      "Could not load watch events: " + e.message));
  }
}

function watchEventEl(ev) {
  const wrap = el("div", "watch-event");

  const head = el("div", "watch-event-head");
  const left = el("div");
  left.appendChild(el("div", "watch-event-tab", ev.title || ev.url || "Unknown tab"));
  if (ev.url) left.appendChild(el("div", "watch-event-url", ev.url));
  head.appendChild(left);
  if (ev.received_at) {
    const t = new Date(ev.received_at);
    const ago = timeAgo(t);
    head.appendChild(el("div", "watch-event-time", ago));
  }
  wrap.appendChild(head);
  wrap.appendChild(el("div", "watch-event-summary", ev.summary || "(no summary)"));
  return wrap;
}

function timeAgo(date) {
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs < 60)   return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return date.toLocaleDateString();
}

$("#watch-refresh").addEventListener("click", loadWatch);

// "Ask Scrappy" in Watch tab — sends a chat message and switches to Chat tab.
$("#watch-send-btn").addEventListener("click", () => {
  const msg = $("#watch-prompt-input").value.trim();
  if (!msg) { toast("Type a message first.", true); return; }
  $("#watch-prompt-input").value = "";
  // Switch to Chat tab.
  document.querySelector('.tab[data-tab="chat"]').click();
  sendChat(msg);
});
$("#watch-prompt-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("#watch-send-btn").click();
  }
});

// ── system ────────────────────────────────────────────────────────
async function loadSystem() {
  // Health
  try {
    const h = await apiJson("/v1/health");
    const box = $("#sys-health");
    box.innerHTML = "";

    const addRow = (k, v, ok) => {
      const row = el("div", "row");
      row.appendChild(el("span", null, k));
      const val = el("span", "mono");
      val.style.color = ok ? "var(--accent)" : "var(--danger)";
      val.textContent = String(v);
      row.appendChild(val);
      box.appendChild(row);
    };

    addRow("api", h.api, h.api === "ok");
    if (h.llm) {
      addRow("llm", h.llm.ok ? "ok" : "down", !!h.llm.ok);
      if (h.llm.model)    addRow("  model", h.llm.model, true);
      if (h.llm.provider) addRow("  provider", h.llm.provider, true);
    }
    if (h.embedder) {
      addRow("embedder", h.embedder.ok ? "ok" : "down", !!h.embedder.ok);
      if (h.embedder.model)    addRow("  model", h.embedder.model, true);
      if (h.embedder.provider) addRow("  provider", h.embedder.provider, true);
      if (h.embedder.dim)      addRow("  dim", h.embedder.dim, true);
    }
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
      const pill = el("span",
        "badge-pill " + (x.available ? "pill-on" : "pill-off"),
        x.available ? "available" : "needs: " + x.missing_connectors.join(", "));
      head.appendChild(pill);
      u.appendChild(head);
      if (x.expertise) u.appendChild(el("div", "unit-desc", x.expertise));
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
        span.innerHTML = `${c.name}.${t.name} `;
        const cls = t.executor === "server" ? "pill-srv" : "pill-cli";
        span.appendChild(el("span", "badge-pill " + cls, t.executor));
        if (t.requires_approval)
          span.appendChild(el("span", "badge-pill pill-appr", "approval"));
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

$("#settings-btn").addEventListener("click", () => openModal());
$("#key-close").addEventListener("click", () => ($("#modal").hidden = true));

$("#modal").addEventListener("click", (e) => {
  if (e.target === $("#modal")) $("#modal").hidden = true;
});

$("#key-save").addEventListener("click", async () => {
  setKey($("#key-input").value.trim());
  $("#modal").hidden = true;
  toast("Key saved.");
  await loadIdentity();
  // Force refresh of data-tabs that depend on auth.
  loaded.system = false;
  loaded.memory = false;
  if ($(".tab[data-tab=system]").classList.contains("active")) {
    loadSystem(); loaded.system = true;
  }
  if ($(".tab[data-tab=memory]").classList.contains("active")) {
    loadMemory(); loaded.memory = true;
  }
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
