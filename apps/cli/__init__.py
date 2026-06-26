"""Terminal client for Scrappy Singh.

Usage:
  scrappy "your message"       one-shot — stream reply, exit
  scrappy                      interactive REPL (Ctrl+C or /exit to quit)
  scrappy agent "task"         spawn an agent and stream its work live
  scrappy agents               list recent agents with status
  scrappy watch <id>           tail a running or finished agent's log
  scrappy worker               run the local executor — agents run on THIS Mac
  scrappy --consolidate        trigger nightly memory consolidation
  scrappy --new                clear session, start fresh

Environment:
  VAULT_API_BASE       server base URL (default: http://127.0.0.1:8000)
  VAULT_API_KEY        bearer token   (default: empty = open mode)
  SCRAPPY_CLAUDE_FLAGS extra flags passed to `claude -p` by the worker
                       (e.g. "--permission-mode acceptEdits")
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

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
            extracted = d.get("extracted", 0)
            saved = d.get("saved", 0)
            skipped = d.get("skipped", 0)
            print(f"Consolidated — extracted: {extracted}, saved: {saved}, skipped: {skipped}")
    except httpx.ConnectError:
        print(f"{_RED}Cannot connect to {API_BASE} — is the server running?{_RESET}")
    except httpx.HTTPStatusError as e:
        print(f"{_RED}HTTP {e.response.status_code}: {e.response.text[:200]}{_RESET}")
    except Exception as e:
        print(f"{_RED}Consolidation failed: {e}{_RESET}")


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


def _run_local_command(kind: str, cmd: str, timeout: int, agent_id: str) -> str:
    """Execute one agent command locally and return combined stdout+stderr."""
    workdir = WORKSPACE / agent_id
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except Exception:
        workdir = WORKSPACE
    shell = os.environ.get("SHELL", "/bin/bash")
    if kind == "claude":
        flags = os.environ.get("SCRAPPY_CLAUDE_FLAGS", "")
        full = f"claude -p {shlex.quote(cmd)} {flags}".strip()
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


async def _worker() -> None:
    """Long-running local executor: agent bash/claude commands run on this Mac."""
    headers: dict[str, str] = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    print(f"{_BOLD}Scrappy worker{_RESET}  →  {API_BASE}")
    print(f"{_DIM}Workspace: {WORKSPACE}{_RESET}")

    async with httpx.AsyncClient() as client:
        try:
            h = await client.get(f"{API_BASE}/v1/health", headers=headers, timeout=10)
            h.raise_for_status()
        except Exception as e:
            print(f"{_RED}Cannot reach {API_BASE} — start the server first.\n  {e}{_RESET}")
            return
        print(f"{_GREEN}connected — waiting for tasks (Ctrl+C to stop){_RESET}\n")

        stop = asyncio.Event()
        current: dict = {"id": None}

        async def heartbeat() -> None:
            while not stop.is_set():
                try:
                    await client.post(
                        f"{API_BASE}/v1/worker/heartbeat", headers=headers, timeout=10
                    )
                except Exception:
                    pass
                await asyncio.sleep(15)

        async def command_loop() -> None:
            while not stop.is_set():
                try:
                    r = await client.get(
                        f"{API_BASE}/v1/worker/next", headers=headers, timeout=35
                    )
                except (httpx.ReadTimeout, httpx.ConnectTimeout):
                    continue
                except httpx.HTTPError as e:
                    print(f"{_RED}poll error: {e}{_RESET}")
                    await asyncio.sleep(2)
                    continue
                if r.status_code == 401:
                    print(f"{_RED}401 Unauthorized — set VAULT_API_KEY{_RESET}")
                    stop.set()
                    return
                if r.status_code == 204:
                    continue
                try:
                    cmd = r.json()
                except Exception:
                    continue

                _display_command(cmd, current)
                output = await asyncio.to_thread(
                    _run_local_command,
                    cmd.get("kind", "bash"),
                    cmd.get("cmd", ""),
                    int(cmd.get("timeout", 60) or 60),
                    cmd.get("agent_id", "misc"),
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

        try:
            await asyncio.gather(heartbeat(), command_loop())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            stop.set()


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


def main() -> None:
    args = sys.argv[1:]

    if "--consolidate" in args:
        asyncio.run(_consolidate())
        return

    if "--new" in args:
        SESSION_FILE.unlink(missing_ok=True)
        args = [a for a in args if a != "--new"]

    # Subcommands: worker / agents / agent / watch
    if args and args[0] == "worker":
        try:
            asyncio.run(_worker())
        except KeyboardInterrupt:
            print("\nworker stopped.")
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
