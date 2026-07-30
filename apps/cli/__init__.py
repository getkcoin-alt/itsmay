"""Terminal client for Scrappy Singh.

Usage:
  scrappy "your message"       one-shot — stream reply, exit
  scrappy                      interactive REPL (Ctrl+C or /exit to quit)
  scrappy agent "task"         spawn an agent and stream its work live
  scrappy agents               list recent agents with status
  scrappy watch <id>           tail a running or finished agent's log
  scrappy voice                start the voice loop — talk to Scrappy out loud
  scrappy serve                run the backend locally (sovereign, no Railway)
  scrappy up                   run the backend + worker together (one terminal)
  scrappy worker               run the local executor — agents run on THIS Mac
  scrappy status               server health + worker connected + memory count
  scrappy awaken               speak his first words (writes birth memories on first run)
  scrappy freeze / unfreeze    kill switch for self-modification (Epic 5 guardrail)
  scrappy secret <name>        give Scrappy a third-party API key (value never shown to him)
  scrappy seed                 populate long-term memory (RAG) from knowledge.yaml
  scrappy --consolidate        trigger nightly memory consolidation
  scrappy --new                clear session, start fresh

Environment:
  VAULT_API_BASE       server base URL (default: http://127.0.0.1:8000)
  VAULT_API_KEY        bearer token   (default: empty = open mode)
  SCRAPPY_CLAUDE_FLAGS flags for the `claude` CLI Scrappy drives
                       (default: --dangerously-skip-permissions, autonomous)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Repo-local .env (dev), then the installed user config. load_dotenv never
# overrides an already-set var, so a real `export` and dev's .env both win over
# ~/.itsmay/config.env — which is the zero-export default for installed users.
load_dotenv()
load_dotenv(Path.home() / ".itsmay" / "config.env")

API_BASE = os.environ.get("VAULT_API_BASE", "http://127.0.0.1:8000")
API_KEY = os.environ.get("VAULT_API_KEY", "")
SESSION_FILE = Path.home() / ".vault_zeta_session"

# ANSI — fall back to empty strings if stdout is not a TTY (pipe/redirect).
if sys.stdout.isatty():
    _RESET = "\033[0m"
    _DIM = "\033[2m"
    _GREEN = "\033[32m"
    _CYAN = "\033[36m"
    _RED = "\033[31m"
    _YELLOW = "\033[33m"
    _BOLD = "\033[1m"
else:
    _RESET = _DIM = _GREEN = _CYAN = _RED = _YELLOW = _BOLD = ""


def _auth_headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "text/event-stream"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _load_session() -> str | None:
    try:
        sid = SESSION_FILE.read_text().strip()
        return sid or None
    except FileNotFoundError:
        return None


def _save_session(session_id: str) -> None:
    SESSION_FILE.write_text(session_id)


async def _stream_turn(message: str, session_id: str | None) -> str | None:
    """POST one message to /v1/chat, stream tokens live, return the new session_id."""
    body: dict = {"message": message, "channel": "terminal"}
    if session_id:
        body["session_id"] = session_id

    new_session_id = session_id
    event = "message"

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST", f"{API_BASE}/v1/chat",
                json=body, headers=_auth_headers(),
            ) as resp:
                resp.raise_for_status()
                print(f"\n{_GREEN}Scrappy{_RESET}  ", end="", flush=True)

                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue

                    # SSE spec: strip exactly one leading space from data field.
                    data = line[6:] if line.startswith("data: ") else line[5:]

                    if event == "session":
                        try:
                            new_session_id = json.loads(data)["session_id"]
                            _save_session(new_session_id)
                        except Exception:
                            pass

                    elif event == "token":
                        print(data, end="", flush=True)

                    elif event == "tool_call":
                        try:
                            tc = json.loads(data)
                            args_str = json.dumps(tc.get("arguments", {}))
                            print(
                                f"\n  {_DIM}→ {tc.get('name')}({args_str}){_RESET}",
                                end="", flush=True,
                            )
                        except Exception:
                            pass

                    elif event == "status":
                        try:
                            st = json.loads(data)
                            summary = str(st.get("result", ""))[:100]
                            print(
                                f"\n  {_DIM}✓ {st.get('tool')}: {summary}{_RESET}",
                                end="", flush=True,
                            )
                        except Exception:
                            pass

                    elif event == "done":
                        try:
                            d = json.loads(data)
                            tok_in = d.get("tokens_in", 0)
                            tok_out = d.get("tokens_out", 0)
                            lat = d.get("latency_ms", 0)
                            print(
                                f"\n{_DIM}  [{tok_in} in / {tok_out} out / {lat}ms]{_RESET}",
                                flush=True,
                            )
                        except Exception:
                            print()
                        return new_session_id

                    elif event == "error":
                        try:
                            err = json.loads(data)
                            print(f"\n{_RED}Error: {err.get('error', data)}{_RESET}", flush=True)
                        except Exception:
                            print(f"\n{_RED}Error: {data}{_RESET}", flush=True)
                        return new_session_id

    except httpx.ConnectError:
        print(f"\n{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 401:
            print(f"\n{_RED}401 Unauthorized — set VAULT_API_KEY{_RESET}")
        else:
            print(f"\n{_RED}HTTP {status}: {e.response.text[:200]}{_RESET}")

    return new_session_id


def _budget_pct(k: dict) -> float | None:
    """Percent of today's token budget left for one key (0–100), or None if the
    limit isn't known yet (no call has populated the rate-limit headers)."""
    rem = k.get("remaining_tokens")
    lim = k.get("limit_tokens")
    if isinstance(rem, int) and isinstance(lim, int) and lim > 0:
        return max(0.0, min(100.0, rem / lim * 100.0))
    return None


def _format_key_line(k: dict) -> str:
    """One status line for a single pooled key. Pure (no I/O) so it's unit-testable.

    Shows active/cooling state, the remaining token budget as 'X / Y left (Z%)'
    when the daily limit is known (else 'X tokens left'), the refill window, and a
    ⚠️ low flag under ~15% of budget — so you see the wall coming before you hit it.
    """
    idx = k.get("idx", "?")
    rem = k.get("remaining_tokens")
    lim = k.get("limit_tokens")
    reset = k.get("reset_tokens")
    pct = _budget_pct(k)

    if isinstance(rem, int) and isinstance(lim, int) and lim > 0:
        budget = f"{rem:,} / {lim:,} tokens left ({pct:.0f}%)"
    elif isinstance(rem, int):
        budget = f"{rem:,} tokens left"
    else:
        budget = "tokens unknown"

    low = pct is not None and pct < 15.0
    low_flag = f" {_YELLOW}⚠️ low{_RESET}" if low else ""

    if k.get("active"):
        refill = f" · resets in {reset}" if (reset and low) else ""
        return f"      #{idx} {_GREEN}● active{_RESET}    {_DIM}{budget}{refill}{_RESET}{low_flag}"
    cd = k.get("cooldown_sec_remaining", 0) or 0
    refill = f" · resets in {reset}" if reset else ""
    return (
        f"      #{idx} {_YELLOW}◐ cooling{_RESET}  "
        f"{_DIM}{cd:.0f}s left · {budget}{refill}{_RESET}{low_flag}"
    )


async def _status() -> None:
    """Print server health + Mac-worker presence + memory count in one shot."""
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    def mark(ok: bool | None) -> str:
        if ok is True:
            return f"{_GREEN}✓{_RESET}"
        if ok is False:
            return f"{_RED}✗{_RESET}"
        return f"{_DIM}?{_RESET}"

    print(f"\n{_BOLD}Scrappy status{_RESET}  →  {API_BASE}")
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            h = await client.get(f"{API_BASE}/v1/health", headers=headers, timeout=10)
            h.raise_for_status()
            hd = h.json()
        except httpx.HTTPStatusError as e:
            print(f"  {mark(False)} server — HTTP {e.response.status_code}")
            return
        except Exception as e:
            print(f"  {mark(False)} server unreachable — {e}")
            return

        api_ok = hd.get("api") == "ok"
        llm_ok = bool((hd.get("llm") or {}).get("ok"))
        emb_ok = bool((hd.get("embedder") or {}).get("ok"))
        print(f"  {mark(api_ok)} api    {mark(llm_ok)} llm    {mark(emb_ok)} embedder")

        # Mac worker presence
        try:
            w = (await client.get(f"{API_BASE}/v1/worker/status", headers=headers)).json()
            online = bool(w.get("online"))
            since = w.get("seconds_since_seen")
            if online:
                detail = f" (last seen {since:.0f}s ago)" if since is not None else ""
                print(f"  {mark(True)} mac worker{detail}")
            else:
                print(f"  {mark(False)} mac worker — not connected (run `scrappy worker`)")
        except Exception:
            print(f"  {mark(None)} mac worker — unknown")

        # Memory (RAG) size + which backend is live (sqlite local / postgres)
        mem = hd.get("memory") or {}
        backend = mem.get("backend")
        if backend == "sqlite":
            backend_label = f" {_DIM}· sqlite (local){_RESET}"
        elif backend == "postgres":
            backend_label = f" {_DIM}· postgres{_RESET}"
        else:
            backend_label = ""
        try:
            m = (await client.get(f"{API_BASE}/v1/memory/stats", headers=headers)).json()
            count = int(m.get("count", 0) or 0)
            extra = "" if count else f" {_DIM}(run `scrappy seed`){_RESET}"
            print(f"  {mark(count > 0)} memory — {count} facts stored{backend_label}{extra}")
        except Exception:
            print(f"  {mark(None)} memory — unknown{backend_label}")

        # LLM key pool — each key: active / cooling-down / token budget left.
        pool = (hd.get("llm") or {}).get("key_pool") or {}
        keys = pool.get("keys") or []
        if keys:
            active = sum(1 for k in keys if k.get("active"))
            low = [k for k in keys if (_budget_pct(k) or 100.0) < 15.0]
            print(f"  {mark(active > 0)} keys — {active}/{len(keys)} active")
            if low:
                print(
                    f"      {_YELLOW}⚠️  {len(low)}/{len(keys)} key(s) under 15% of "
                    f"today's token budget — stack more in LLM_API_KEY for headroom"
                    f"{_RESET}"
                )
            for k in keys:
                print(_format_key_line(k))
        elif (hd.get("llm") or {}).get("ok"):
            print(f"  {mark(None)} keys — no pool reported (single key or none)")
    print(f"\n  {_DIM}Designed & Developed by Karnveer Singh · www.karnveer.com{_RESET}")
    print()


def _load_knowledge_entries() -> list[dict] | None:
    """Read scripts/knowledge.yaml from the repo (package- or cwd-relative)."""
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "knowledge.yaml",
        Path.cwd() / "scripts" / "knowledge.yaml",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return None
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or []
    except Exception as e:
        print(f"{_RED}could not read {path}: {e}{_RESET}")
        return None


async def _seed() -> None:
    """Populate RAG from the repo's knowledge.yaml via POST /v1/memory/seed.

    The entries are read locally and shipped in the request, so seeding works
    even though the deployed server image doesn't include scripts/.
    """
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    entries = _load_knowledge_entries()
    payload = {"entries": entries} if entries else {}
    if not entries:
        print(f"{_DIM}no local knowledge.yaml found — asking server for its copy…{_RESET}")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{API_BASE}/v1/memory/seed", headers=headers, json=payload
            )
            resp.raise_for_status()
            d = resp.json()
            if d.get("error"):
                print(f"{_RED}{d['error']}{_RESET}")
                return
            print(
                f"Seeded memory — inserted: {d.get('inserted', 0)}, "
                f"skipped: {d.get('skipped', 0)}, total: {d.get('total', 0)}"
            )
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except Exception as e:
        print(f"{_RED}Seed failed: {e}{_RESET}")


async def _consolidate() -> None:
    """Call POST /v1/memory/consolidate and print the result."""
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{API_BASE}/v1/memory/consolidate", headers=headers)
            resp.raise_for_status()
            d = resp.json()
            facts = d.get("facts", d)  # back-compat if server returns the flat shape
            wf = d.get("workflows", {})
            print(
                f"Consolidated — facts: {facts.get('saved', 0)} saved "
                f"of {facts.get('extracted', 0)}; "
                f"playbooks: {wf.get('saved', 0)} saved of {wf.get('playbooks', 0)} found"
            )
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except Exception as e:
        print(f"{_RED}Consolidation failed: {e}{_RESET}")


async def _awaken() -> None:
    """Call POST /v1/identity/awaken and speak Scrappy's first words."""
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{API_BASE}/v1/identity/awaken", headers=headers)
            resp.raise_for_status()
            d = resp.json()
            inv = d.get("inventory", {})
            if d.get("born"):
                print(
                    f"\n{_BOLD}{_GREEN}⚡ Scrappy is awake.{_RESET}  "
                    f"{_DIM}(born {d.get('born_at')}){_RESET}\n"
                )
            else:
                print(f"\n{_DIM}(already awake){_RESET}\n")
            print(f"{_CYAN}{d.get('first_words', '')}{_RESET}\n")
            print(
                f"{_DIM}v{inv.get('version', '?')} · {inv.get('model', '?')} · "
                f"{len(inv.get('connectors') or [])} connectors · "
                f"{len(inv.get('experts') or [])} experts · "
                f"{inv.get('memory_count', 0)} memories{_RESET}"
            )
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except Exception as e:
        print(f"{_RED}Awaken failed: {e}{_RESET}")


def _freeze() -> None:
    """Engage the self-modification kill switch (local marker the server reads)."""
    from core.identity.self_guard import freeze

    m = freeze()
    print(
        f"{_YELLOW}❄️  Self-modification FROZEN.{_RESET} "
        f"{_DIM}Scrappy can't change his own code until `scrappy unfreeze`."
        f"\n  marker: {m}{_RESET}"
    )


def _unfreeze() -> None:
    """Release the self-modification kill switch."""
    from core.config import get_settings
    from core.identity.self_guard import unfreeze

    if unfreeze():
        print(f"{_GREEN}Self-modification unfrozen.{_RESET}")
    else:
        print(f"{_DIM}Was not frozen.{_RESET}")
    if not get_settings().self_modify:
        print(f"{_YELLOW}Note: SELF_MODIFY is off in config — still disabled.{_RESET}")


async def _secret(name: str) -> None:
    """Give Scrappy a third-party credential. The value is entered here and POSTed
    straight to config — it is never shown to the model."""
    import getpass

    try:
        value = getpass.getpass(f"Value for {name} (hidden, not echoed): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled.")
        return
    if not value:
        print("cancelled (empty).")
        return
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{API_BASE}/v1/identity/secret",
                headers=headers,
                json={"name": name, "value": value},
            )
            resp.raise_for_status()
            d = resp.json()
        print(
            f"{_GREEN}✓ {d.get('name')} is set.{_RESET} "
            f"{_DIM}Scrappy was told it's available — he never saw the value.{_RESET}"
        )
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except Exception as e:
        print(f"{_RED}Failed: {e}{_RESET}")


def _render_agent_event(kind: str, text: str) -> None:
    """Print a single agent log entry with colour and formatting."""
    if kind == "thought":
        print(f"  {_DIM}thinking  {text}{_RESET}", flush=True)
    elif kind == "cmd":
        lines = text.strip().splitlines() or [""]
        first, *rest = lines
        print(f"  {_YELLOW}$ {first}{_RESET}", flush=True)
        for line in rest:
            print(f"  {_YELLOW}  {line}{_RESET}", flush=True)
    elif kind == "output":
        lines = text.strip().splitlines()
        for line in lines[:8]:
            print(f"  {_DIM}> {line[:200]}{_RESET}", flush=True)
        if len(lines) > 8:
            print(f"  {_DIM}> … ({len(lines) - 8} more lines){_RESET}", flush=True)
    elif kind == "result":
        print(f"\n{_GREEN}{_BOLD}{text}{_RESET}", flush=True)
    elif kind == "error":
        print(f"  {_RED}! {text}{_RESET}", flush=True)


async def _stream_agent(task: str) -> None:
    """POST /v1/agents/stream — spawn an agent and stream its work live."""
    body = {"task": task}
    headers: dict[str, str] = {"Accept": "text/event-stream"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    _SEP = "─" * 45
    event = "message"

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST", f"{API_BASE}/v1/agents/stream",
                json=body, headers=headers,
            ) as resp:
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[6:] if line.startswith("data: ") else line[5:]

                    if event == "agent_started":
                        try:
                            d = json.loads(data)
                            print(f"\nSpawning agent {_BOLD}{d['agent_id']}{_RESET}…")
                            print(_SEP)
                        except Exception:
                            pass

                    elif event in ("thought", "cmd", "output", "result", "error"):
                        try:
                            d = json.loads(data)
                            _render_agent_event(event, d.get("text", ""))
                        except Exception:
                            pass

                    elif event == "agent_done":
                        try:
                            d = json.loads(data)
                            status = d.get("status", "unknown")
                            result = d.get("result", "")
                            print(_SEP)
                            color = _GREEN if status == "done" else _RED
                            print(f"{color}Done ({status}){_RESET}")
                            if result and result != "(completed)":
                                print(f"\n{_BOLD}Summary:{_RESET} {result}")
                        except Exception:
                            pass

    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print(f"{_RED}401 Unauthorized — set VAULT_API_KEY{_RESET}")
        else:
            print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except Exception as e:
        print(f"{_RED}Error: {e}{_RESET}")


async def _list_agents() -> None:
    """GET /v1/agents — print a summary table of recent agents."""
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{API_BASE}/v1/agents", headers=headers)
            resp.raise_for_status()
            agents = resp.json()
        if not agents:
            print("No agents yet.")
            return
        print(f"\n{'ID':<10} {'STATUS':<10} {'LOGS':<6} TASK")
        print("─" * 70)
        for a in agents:
            status = a.get("status", "?")
            if status == "done":
                sc = _GREEN
            elif status == "error":
                sc = _RED
            else:
                sc = _DIM
            task = (a.get("task") or "")[:45]
            print(f"{a['id']:<10} {sc}{status:<10}{_RESET} {a.get('log_count', 0):<6} {task}")
        print()
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE}{_RESET}")
    except httpx.HTTPStatusError as e:
        print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")


async def _watch_agent(agent_id: str) -> None:
    """GET /v1/agents/{id}/stream — replay + follow a running or finished agent."""
    headers: dict[str, str] = {"Accept": "text/event-stream"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    _SEP = "─" * 45
    event = "message"

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "GET", f"{API_BASE}/v1/agents/{agent_id}/stream",
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                print(f"\nWatching agent {_BOLD}{agent_id}{_RESET}")
                print(_SEP)

                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[6:] if line.startswith("data: ") else line[5:]

                    if event in ("thought", "cmd", "output", "result", "error"):
                        try:
                            d = json.loads(data)
                            _render_agent_event(event, d.get("text", ""))
                        except Exception:
                            pass

                    elif event == "agent_done":
                        try:
                            d = json.loads(data)
                            status = d.get("status", "unknown")
                            result = d.get("result", "")
                            print(_SEP)
                            color = _GREEN if status == "done" else _RED
                            print(f"{color}Done ({status}){_RESET}")
                            if result and result != "(completed)":
                                print(f"\n{_BOLD}Summary:{_RESET} {result}")
                        except Exception:
                            pass

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            print(f"{_RED}Agent {agent_id!r} not found.{_RESET}")
        elif e.response.status_code == 401:
            print(f"{_RED}401 Unauthorized — set VAULT_API_KEY{_RESET}")
        else:
            print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE}{_RESET}")
    except Exception as e:
        print(f"{_RED}Error: {e}{_RESET}")


# ── local worker: agents' commands run on THIS machine ──────────────
WORKSPACE = Path.home() / "scrappy-workspace"


def _claude_flags() -> str:
    """Flags for the `claude` CLI. Autonomous by default (skip permission prompts)
    so headless Claude Code runs to completion with no human to approve."""
    return os.environ.get("SCRAPPY_CLAUDE_FLAGS") or "--dangerously-skip-permissions"


def _worker_workdir(agent_id: str) -> Path:
    """A working directory for an agent's commands that we KNOW is writable.

    The default is ~/scrappy-workspace/<agent_id>, but if that can't be created
    or written to — bad perms, a read-only mount, ownership left behind by an
    earlier run as root — fall back to a fresh temp dir. A Claude Code build
    should produce the result, never die on a "write permission issue in the
    workspace."
    """
    candidate = WORKSPACE / (agent_id or "misc")
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".scrappy_write_test"
        probe.write_text("ok")
        probe.unlink()
        return candidate
    except Exception:
        return Path(tempfile.mkdtemp(prefix="scrappy-build-"))


def _run_claude_streaming(
    prompt: str,
    workdir: Path,
    shell: str,
    timeout: int,
    progress_cb: Callable[[str], None] | None,
) -> str:
    """Run headless Claude Code in stream-json mode, calling `progress_cb` with a
    live milestone for each step as it happens, and return the final assistant
    text (which carries the SCRAPPY_RESULT line coder.build parses).

    `--output-format stream-json --verbose` makes `claude -p` emit one JSON event
    per line as it works, instead of printing nothing until the very end.
    """
    from core.connectors.coder.stream import parse_stream_line

    full = (
        f"claude -p {shlex.quote(prompt)} "
        f"--output-format stream-json --verbose {_claude_flags()}"
    ).strip()
    try:
        proc = subprocess.Popen(
            [shell, "-lc", full],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
            bufsize=1,
        )
    except FileNotFoundError:
        return f"[worker: shell {shell!r} not found]"
    except Exception as e:
        return f"[worker error: {type(e).__name__}: {e}]"

    # A watchdog gives us a hard timeout even if Claude hangs mid-stream with no
    # newline (a plain readline loop would block forever otherwise).
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        with contextlib.suppress(Exception):
            proc.kill()

    timer = threading.Timer(max(1, timeout), _kill)
    timer.start()
    final_text: str | None = None
    raw_tail: list[str] = []  # fallback if no terminal result event arrives
    try:
        for line in proc.stdout or ():
            raw_tail.append(line[:2000])
            if len(raw_tail) > 200:
                raw_tail.pop(0)
            parsed = parse_stream_line(line)
            for m in parsed.milestones:
                if progress_cb:
                    progress_cb(m)
            if parsed.final_text is not None:
                final_text = parsed.final_text
        proc.wait()
    except Exception as e:
        return f"[worker error: {type(e).__name__}: {e}]"
    finally:
        timer.cancel()

    if timed_out.is_set():
        return f"[timed out after {timeout}s]"
    if final_text is not None:
        return final_text.strip() or "(no output)"
    # No result event (older CLI, an error, or non-stream output): return the raw
    # TAIL so a SCRAPPY_RESULT printed at the end still survives for parsing.
    raw = "".join(raw_tail).strip()
    return (raw[-4096:] if raw else "") or "(no output)"


def _run_local_command(
    kind: str,
    cmd: str,
    timeout: int,
    agent_id: str,
    *,
    stream: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> str:
    """Execute one agent command locally and return combined stdout+stderr.

    `stream=True` (set for coder.build) runs Claude in streaming mode so its
    progress is narrated live; everything else keeps the simple blocking path.
    """
    workdir = _worker_workdir(agent_id)
    shell = os.environ.get("SHELL", "/bin/bash")
    if kind == "claude" and stream:
        return _run_claude_streaming(cmd, workdir, shell, timeout, progress_cb)
    if kind == "claude":
        full = f"claude -p {shlex.quote(cmd)} {_claude_flags()}".strip()
    else:
        full = cmd
    try:
        proc = subprocess.run(
            [shell, "-lc", full],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"[timed out after {timeout}s]"
    except FileNotFoundError:
        return f"[worker: shell {shell!r} not found]"
    except Exception as e:
        return f"[worker error: {type(e).__name__}: {e}]"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if len(out) > 4096:
        out = out[:4096] + "\n…[truncated]"
    return out or "(no output)"


def _display_command(cmd: dict, current: dict) -> None:
    if cmd.get("agent_id") != current.get("id"):
        current["id"] = cmd.get("agent_id")
        print(f"\n{_CYAN}[agent {cmd.get('agent_id')}]{_RESET}  {cmd.get('task', '')}")
    thought = (cmd.get("thought") or "").strip()
    if thought:
        print(f"  {_DIM}thinking  {thought[:300]}{_RESET}", flush=True)
    if cmd.get("kind") == "claude":
        print(f"  {_YELLOW}▸ claude{_RESET} {_DIM}{cmd.get('cmd', '')[:200]}{_RESET}", flush=True)
    else:
        print(f"  {_YELLOW}$ {cmd.get('cmd', '')}{_RESET}", flush=True)


def _display_output(output: str) -> None:
    lines = output.splitlines()
    for line in lines[:12]:
        print(f"  {_DIM}> {line[:200]}{_RESET}", flush=True)
    if len(lines) > 12:
        print(f"  {_DIM}> … ({len(lines) - 12} more lines){_RESET}", flush=True)


class _WorkerAuthError(Exception):
    """Fatal: the server rejected our credentials; no point reconnecting."""


async def _serve(client: httpx.AsyncClient, headers: dict, current: dict) -> None:
    """Run heartbeat + command loop until the connection fails, then raise.

    A persistent poll failure raises ConnectionError so the caller reconnects;
    a 401 raises _WorkerAuthError so the caller stops for good.
    """
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await client.post(
                    f"{API_BASE}/v1/worker/heartbeat", headers=headers, timeout=10
                )
            except Exception:
                pass  # the command loop owns connection-health decisions
            try:
                await asyncio.wait_for(stop.wait(), timeout=15)
            except TimeoutError:
                pass

    async def command_loop() -> None:
        fails = 0
        while not stop.is_set():
            try:
                r = await client.get(
                    f"{API_BASE}/v1/worker/next", headers=headers, timeout=35
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout):
                continue  # long-poll window expired — just re-poll
            except (httpx.HTTPError, OSError):
                fails += 1
                if fails >= 3:
                    raise ConnectionError("worker poll failed repeatedly") from None
                await asyncio.sleep(1)
                continue
            fails = 0
            if r.status_code == 401:
                raise _WorkerAuthError
            if r.status_code == 204:
                continue
            if r.status_code >= 500:
                await asyncio.sleep(1)  # server hiccup — stay connected, retry
                continue
            try:
                cmd = r.json()
            except Exception:
                continue

            _display_command(cmd, current)
            stream = bool(cmd.get("stream_progress"))
            cmd_id = cmd.get("command_id")

            def _post_progress(chunk: str, _id=cmd_id) -> None:
                # Runs in the worker thread; a synchronous POST is fine here.
                # Best-effort — a dropped milestone never affects the result.
                print(f"  {_DIM}… {chunk[:160]}{_RESET}", flush=True)
                with contextlib.suppress(Exception):
                    httpx.post(
                        f"{API_BASE}/v1/worker/progress",
                        json={"command_id": _id, "chunk": chunk},
                        headers=headers,
                        timeout=5,
                    )

            output = await asyncio.to_thread(
                _run_local_command,
                cmd.get("kind", "bash"),
                cmd.get("cmd", ""),
                int(cmd.get("timeout", 60) or 60),
                cmd.get("agent_id", "misc"),
                stream=stream,
                progress_cb=_post_progress if stream else None,
            )
            _display_output(output)
            try:
                await client.post(
                    f"{API_BASE}/v1/worker/result",
                    json={"command_id": cmd.get("command_id"), "output": output},
                    headers=headers,
                    timeout=15,
                )
            except Exception as e:
                print(f"{_RED}failed to post result: {e}{_RESET}")

    hb = asyncio.create_task(heartbeat())
    try:
        await command_loop()
    finally:
        stop.set()
        hb.cancel()
        try:
            await hb
        except (asyncio.CancelledError, Exception):
            pass


async def _worker() -> None:
    """Local executor with auto-reconnect: agent commands run on THIS Mac.

    Survives server restarts and network drops — keeps retrying with exponential
    backoff (2→4→…→30s) instead of exiting, so you can leave it running.
    """
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    print(f"{_BOLD}Scrappy worker{_RESET}  →  {API_BASE}")
    print(f"{_DIM}Workspace: {WORKSPACE}  ·  Ctrl+C to stop{_RESET}")

    current: dict = {"id": None}
    backoff = 2
    max_backoff = 30
    connected = False

    try:
        async with httpx.AsyncClient() as client:
            while True:
                # ── (re)establish the connection ───────────────────
                try:
                    h = await client.get(
                        f"{API_BASE}/v1/health", headers=headers, timeout=10
                    )
                    if h.status_code == 401:
                        raise _WorkerAuthError
                    h.raise_for_status()
                except _WorkerAuthError:
                    print(f"{_RED}401 Unauthorized — check VAULT_API_KEY. Stopping.{_RESET}")
                    return
                except Exception as e:
                    if connected:
                        print(f"\n{_RED}✗ disconnected{_RESET} — {e}")
                        connected = False
                    print(f"{_DIM}reconnecting in {backoff}s…{_RESET}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
                    continue

                if not connected:
                    print(f"{_GREEN}✓ connected — waiting for tasks{_RESET}\n")
                    connected = True
                backoff = 2  # reset after a healthy connection

                # ── serve until the connection drops ───────────────
                try:
                    await _serve(client, headers, current)
                except _WorkerAuthError:
                    print(f"{_RED}401 Unauthorized — check VAULT_API_KEY. Stopping.{_RESET}")
                    return
                except (httpx.HTTPError, OSError) as e:
                    print(f"\n{_RED}✗ disconnected{_RESET} — {e}")
                    connected = False
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nworker stopped.")


async def _repl() -> None:
    """Interactive REPL — keeps session alive across turns."""
    session_id = _load_session()
    print(f"{_BOLD}Scrappy Singh{_RESET}  {_DIM}/exit to quit · /new for fresh session{_RESET}")
    if session_id:
        print(f"{_DIM}Session {session_id}{_RESET}")
    print()

    while True:
        try:
            user_input = input(f"{_CYAN}you ›{_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("bye.")
            break

        if user_input == "/new":
            session_id = None
            SESSION_FILE.unlink(missing_ok=True)
            print(f"{_DIM}New session.{_RESET}\n")
            continue

        session_id = await _stream_turn(user_input, session_id)
        print()


def _serve_api(run=None) -> None:
    """Run the backend on THIS machine — the sovereign, no-Railway path.

    Binds localhost by default (single-operator, not exposed on the LAN); memory
    uses the local SQLite file unless a DATABASE_URL is configured. `run` is the
    server entrypoint (defaults to uvicorn.run) — injectable for tests.
    """
    from core.config import get_settings
    from core.memory.backend import describe_backend

    if run is None:
        try:
            import uvicorn

            run = uvicorn.run
        except ImportError:
            print(f"{_RED}uvicorn isn't installed — run: pip install -e .{_RESET}")
            return

    s = get_settings()
    host = os.environ.get("SCRAPPY_SERVE_HOST", "127.0.0.1")
    port = int(os.environ.get("SCRAPPY_SERVE_PORT", "8000"))
    mem = describe_backend()
    print(f"{_GREEN}{_BOLD}Scrappy backend{_RESET}  →  http://{host}:{port}")
    print(f"  {_DIM}memory: {mem['backend']} ({mem['location']}){_RESET}")
    if not s.llm_api_key:
        print(
            f"  {_YELLOW}⚠️  no LLM_API_KEY set — add one to ~/.itsmay/config.env "
            f"(free Groq key at console.groq.com){_RESET}"
        )
    print(
        f"  {_DIM}other terminals use the default "
        f"VAULT_API_BASE=http://{host}:{port}{_RESET}\n"
    )
    try:
        run("apps.api.main:app", host=host, port=port, log_level=s.log_level.lower())
    except KeyboardInterrupt:
        print("\nbackend stopped.")


async def _up(run_server=None, run_worker=None) -> None:
    """Run the brain (backend) + the local executor (worker) in one process —
    one terminal instead of two. Talk to it from another terminal with
    `scrappy voice` (or `scrappy "..."`).

    Both run in a single event loop: uvicorn's `Server.serve()` coroutine
    alongside the worker's long-poll loop. The worker starts only once the API is
    accepting connections, and is cancelled when the server stops. `run_server` /
    `run_worker` are injectable for tests.
    """
    global API_BASE
    from core.config import get_settings
    from core.memory.backend import describe_backend

    s = get_settings()
    host = os.environ.get("SCRAPPY_SERVE_HOST", "127.0.0.1")
    port = int(os.environ.get("SCRAPPY_SERVE_PORT", "8000"))
    # Point the worker (and anything reading the env) at the server we launch.
    API_BASE = f"http://{host}:{port}"
    os.environ["VAULT_API_BASE"] = API_BASE

    if run_server is None:
        try:
            import uvicorn
        except ImportError:
            print(f"{_RED}uvicorn isn't installed — run: pip install -e .{_RESET}")
            return
        config = uvicorn.Config(
            "apps.api.main:app", host=host, port=port, log_level=s.log_level.lower()
        )
        run_server = uvicorn.Server(config).serve
    if run_worker is None:
        run_worker = _worker

    mem = describe_backend()
    print(
        f"{_GREEN}{_BOLD}Scrappy up{_RESET}  →  http://{host}:{port}  "
        f"{_DIM}(brain + worker){_RESET}"
    )
    print(f"  {_DIM}memory: {mem['backend']} ({mem['location']}){_RESET}")
    from core.identity.restart import clear_restart, pending_restart

    resumed = pending_restart()
    if resumed:
        clear_restart()
        print(f"  {_GREEN}↻ resumed after a self-update{_RESET} {_DIM}({resumed}){_RESET}")
    if not s.llm_api_key:
        print(
            f"  {_YELLOW}⚠️  no LLM_API_KEY set — add one to ~/.itsmay/config.env "
            f"(free Groq key at console.groq.com){_RESET}"
        )
    print(f"  {_DIM}talk to it in another terminal:  scrappy voice{_RESET}\n")

    # Run both; the server owns the lifetime (Ctrl+C → uvicorn shuts it down). The
    # worker's own reconnect-backoff covers the brief window before the API binds,
    # so there's no need to gate its start on readiness.
    server_task = asyncio.create_task(run_server())
    worker_task = asyncio.create_task(run_worker())
    try:
        await server_task
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await worker_task


def main() -> None:
    args = sys.argv[1:]

    if "--consolidate" in args:
        asyncio.run(_consolidate())
        return

    if "--new" in args:
        SESSION_FILE.unlink(missing_ok=True)
        args = [a for a in args if a != "--new"]

    # Subcommands: voice / worker / status / seed / agents / agent / watch
    if args and args[0] == "voice":
        try:
            from apps.mac_agent.voice_loop import main as voice_main
        except ImportError as e:
            print(f"{_RED}voice needs the mac extras — run: pip install -e '.[mac]'"
                  f"\n  ({e}){_RESET}")
            return
        try:
            asyncio.run(voice_main())
        except KeyboardInterrupt:
            pass
        return

    if args and args[0] == "worker":
        try:
            asyncio.run(_worker())
        except KeyboardInterrupt:
            print("\nworker stopped.")
        return

    if args and args[0] == "serve":
        _serve_api()
        return

    if args and args[0] == "up":
        try:
            asyncio.run(_up())
        except KeyboardInterrupt:
            print("\nstopped.")
        return

    if args and args[0] == "status":
        asyncio.run(_status())
        return

    if args and args[0] == "seed":
        asyncio.run(_seed())
        return

    if args and args[0] == "awaken":
        asyncio.run(_awaken())
        return

    if args and args[0] == "freeze":
        _freeze()
        return

    if args and args[0] == "unfreeze":
        _unfreeze()
        return

    if args and args[0] == "secret":
        if len(args) < 2:
            print(f"{_RED}Usage: scrappy secret <name>  (e.g. elevenlabs_api_key){_RESET}")
            return
        asyncio.run(_secret(args[1]))
        return

    if args and args[0] == "agents":
        asyncio.run(_list_agents())
        return

    if args and args[0] == "watch":
        if len(args) < 2:
            print(f"{_RED}Usage: scrappy watch <agent_id>{_RESET}")
            return
        asyncio.run(_watch_agent(args[1]))
        print()
        return

    if args and args[0] == "agent":
        task = " ".join(args[1:]).strip()
        if not task:
            print(f"{_RED}Usage: scrappy agent <task description>{_RESET}")
            return
        asyncio.run(_stream_agent(task))
        print()
        return

    non_flag_args = [a for a in args if not a.startswith("--")]
    if non_flag_args:
        message = " ".join(non_flag_args)
        session_id = _load_session()
        asyncio.run(_stream_turn(message, session_id))
        print()
        return

    asyncio.run(_repl())


if __name__ == "__main__":
    main()
