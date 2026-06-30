"""Mini AI device app — the local companion you run on your Mac (later: an appliance).

Commands:
  mini run                 the companion: replies as you talk · press O to observe
                           (listen+remember silently) · press V to cycle the voice
  mini enroll              add a person: capture their voice + give the bot a nickname
  mini profiles            list enrolled people (nickname + personality each chose)
  mini personas            list the personalities you can pick at enrollment
  mini memories <id>       show what the bot remembers about a profile
  mini forget <id>         delete a person entirely (voice + memories)
  mini reset               wipe ALL profiles + memories (start fresh)

Fully local: Ollama LLM + faster-whisper STT + Piper TTS + fastembed memory, one
SQLite file per device (COMPANION_SQLITE_PATH). Nothing leaves the machine.

`run`/`enroll` need a mic (the `[mini]` extra). The read-only DB commands work
without any audio stack.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path.home() / ".itsmay" / "config.env")

# ANSI (skip when piped)
if sys.stdout.isatty():
    _RESET, _DIM, _GREEN, _CYAN, _YELLOW, _BOLD = (
        "\033[0m",
        "\033[2m",
        "\033[32m",
        "\033[36m",
        "\033[33m",
        "\033[1m",
    )
else:
    _RESET = _DIM = _GREEN = _CYAN = _YELLOW = _BOLD = ""


async def _list_profiles() -> None:
    from core.companion.profiles import ProfileStore, ensure_profile_schema
    from core.config import get_settings

    path = ensure_profile_schema(get_settings().companion_sqlite_path)
    profiles = await ProfileStore(path).all()
    if not profiles:
        print(f"{_DIM}No profiles yet — run `mini enroll` to add someone.{_RESET}")
        return
    from core.companion.persona import persona_title

    print(f"\n{_BOLD}Enrolled people{_RESET}  {_DIM}({path}){_RESET}")
    for p in profiles:
        name = p.person_name or "(unnamed)"
        nick = p.bot_nickname or "(no nickname)"
        seen = p.last_seen_at.strftime("%Y-%m-%d") if p.last_seen_at else "?"
        print(f"  {_GREEN}{name}{_RESET} — calls me {_CYAN}{nick}{_RESET} "
              f"{_DIM}({persona_title(p.persona)}){_RESET}  "
              f"{_DIM}id={p.id[:8]} · {p.sample_count} samples · last {seen}{_RESET}")
    print()


async def _list_personas() -> None:
    from core.companion.persona import PERSONAS
    from core.config import get_settings

    default = get_settings().companion_persona
    print(f"\n{_BOLD}Personalities{_RESET}  {_DIM}(pick one per person at `mini enroll`){_RESET}")
    for key, (title, _file) in PERSONAS.items():
        star = f" {_GREEN}(default){_RESET}" if key == default else ""
        print(f"  {_CYAN}{key}{_RESET} — {title}{star}")
    print()


async def _show_memories(profile_id: str) -> None:
    from core.companion.profiles import ProfileStore, ensure_profile_schema
    from core.config import get_settings
    from core.memory.sqlite_store import SqliteSemanticStore

    path = ensure_profile_schema(get_settings().companion_sqlite_path)
    profile = await _resolve(ProfileStore(path), profile_id)
    if profile is None:
        print(f"{_YELLOW}No profile matches {profile_id!r}.{_RESET}")
        return
    rows = await SqliteSemanticStore(path).list_recent(profile.user_id, limit=40)
    who = profile.person_name or profile.bot_nickname or profile.id[:8]
    print(f"\n{_BOLD}What I remember about {who}{_RESET}  {_DIM}({len(rows)} shown){_RESET}")
    for r in rows:
        print(f"  • {r.content}  {_DIM}[{r.kind} {r.importance:.1f}]{_RESET}")
    print()


async def _forget(profile_id: str) -> None:
    from core.companion.profiles import ProfileStore, ensure_profile_schema
    from core.config import get_settings

    path = ensure_profile_schema(get_settings().companion_sqlite_path)
    store = ProfileStore(path)
    profile = await _resolve(store, profile_id)
    if profile is None:
        print(f"{_YELLOW}No profile matches {profile_id!r}.{_RESET}")
        return
    ok = await store.forget(profile.id)
    who = profile.person_name or profile.bot_nickname or profile.id[:8]
    print(f"{_GREEN}Forgot {who} — voice and memories erased.{_RESET}" if ok
          else f"{_YELLOW}Nothing to forget.{_RESET}")


async def _reset() -> None:
    from core.companion.profiles import ProfileStore, ensure_profile_schema
    from core.config import get_settings

    path = ensure_profile_schema(get_settings().companion_sqlite_path)
    store = ProfileStore(path)
    profiles = await store.all()
    if not profiles:
        print(f"{_DIM}Already empty — nothing to reset.{_RESET}")
        return
    ans = (
        await asyncio.to_thread(
            input, f"Erase ALL {len(profiles)} profile(s) + their memories? [y/N] "
        )
    ).strip().lower()
    if ans != "y":
        print("Cancelled.")
        return
    n = await store.wipe_all()
    print(f"{_GREEN}Reset — erased {n} profile(s). Start fresh with `mini enroll`.{_RESET}")


async def _resolve(store, profile_id: str):
    """Match a full id or a short id prefix to a profile."""
    for p in await store.all():
        if p.id == profile_id or p.id.startswith(profile_id):
            return p
    return None


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "run"

    if cmd == "profiles":
        asyncio.run(_list_profiles())
        return
    if cmd == "personas":
        asyncio.run(_list_personas())
        return
    if cmd == "reset":
        asyncio.run(_reset())
        return
    if cmd == "memories":
        if len(args) < 2:
            print(f"{_YELLOW}Usage: mini memories <profile_id>{_RESET}")
            return
        asyncio.run(_show_memories(args[1]))
        return
    if cmd == "forget":
        if len(args) < 2:
            print(f"{_YELLOW}Usage: mini forget <profile_id>{_RESET}")
            return
        asyncio.run(_forget(args[1]))
        return
    if cmd in ("run", "enroll"):
        try:
            from apps.mini.loop import enroll_flow, run_loop
        except ImportError as e:
            print(f"{_YELLOW}Voice needs the companion extra — "
                  f"pip install -e '.[mini]'\n  ({e}){_RESET}")
            return
        try:
            asyncio.run(enroll_flow() if cmd == "enroll" else run_loop())
        except KeyboardInterrupt:
            print("\nbye.")
        return

    print(__doc__)
