"""Terminal client for Scrappy Singh.

Usage:
  scrappy "your message"    one-shot — stream reply, exit
  scrappy                   interactive REPL (Ctrl+C or /exit to quit)
  scrappy --consolidate     trigger nightly memory consolidation
  scrappy --new             clear session, start fresh

Environment:
  VAULT_API_BASE  server base URL (default: http://127.0.0.1:8000)
  VAULT_API_KEY   bearer token   (default: empty = open mode)
"""

from __future__ import annotations

import asyncio
import json
import os
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
    _BOLD = "\033[1m"
else:
    _RESET = _DIM = _GREEN = _CYAN = _RED = _BOLD = ""


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
