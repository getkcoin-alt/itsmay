"""AppleScript bridge for Google Chrome.

Two public helpers:
- list_tabs()            → [{win, tab, title, url}, ...]
- read_tab_text(win, tab) → str  (page innerText via JS injection)

Uses `osascript` — no Chrome extension required, no DevTools port.
"""

from __future__ import annotations

import subprocess


def _osascript(script: str) -> str:
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip()


def list_tabs() -> list[dict]:
    """Return all open Chrome tabs as [{win, tab, title, url}].

    Uses ||| as a field separator so commas in titles/URLs don't break parsing.
    """
    script = """
tell application "Google Chrome"
    set output to ""
    set winIdx to 0
    repeat with w in windows
        set winIdx to winIdx + 1
        set tabIdx to 0
        repeat with t in tabs of w
            set tabIdx to tabIdx + 1
            set output to output & winIdx & "|||" & tabIdx & "|||" & (title of t) & "|||" & (URL of t) & "\n"
        end repeat
    end repeat
end tell
return output
"""
    raw = _osascript(script)
    tabs: list[dict] = []
    for line in raw.splitlines():
        parts = line.split("|||", 3)
        if len(parts) == 4:
            try:
                tabs.append(
                    {
                        "win": int(parts[0]),
                        "tab": int(parts[1]),
                        "title": parts[2],
                        "url": parts[3],
                    }
                )
            except ValueError:
                pass
    return tabs


def format_tab_list(tabs: list[dict]) -> str:
    if not tabs:
        return "No Chrome tabs found (is Chrome running?)"
    lines = ["Open Chrome tabs:"]
    for t in tabs:
        lines.append(f"  win={t['win']} tab={t['tab']}  {t['title']}  ({t['url']})")
    return "\n".join(lines)


def read_tab_text(win: int, tab: int) -> str:
    """Return the visible text content of a Chrome tab via JavaScript."""
    script = f"""tell application "Google Chrome"
    return execute (tab {tab} of window {win}) javascript "document.body.innerText"
end tell"""
    return _osascript(script)
