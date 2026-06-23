"""Background Chrome tab watcher.

Usage:
    watcher = TabWatcher(win=1, tab=2, interval=120,
                         api_base="https://...", api_key="...")
    watcher.start()   # returns immediately; polling runs in a daemon thread
    watcher.stop()    # signals the thread to exit

On each poll cycle:
1. Reads the tab's current text content via AppleScript.
2. Computes a unified diff against the previous snapshot.
3. If content changed, POSTs {title, url, snapshot, diff} to
   `POST /v1/browser/tab-event` on the Vault Zeta server.
4. The server runs LLM analysis and returns a summary string.
5. That summary is shown as a macOS notification and printed to stdout.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import threading

import httpx

from apps.mac_agent.chrome import read_tab_text


class TabWatcher:
    def __init__(
        self,
        win: int,
        tab: int,
        interval: int = 120,
        *,
        api_base: str,
        api_key: str = "",
        title: str = "",
        url: str = "",
    ) -> None:
        self.win = win
        self.tab = tab
        self.interval = max(10, interval)
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.title = title
        self.url = url
        self._last_content = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="tab-watcher"
        )

    # ── public ────────────────────────────────────────────────────

    def start(self) -> str:
        self._last_content = read_tab_text(self.win, self.tab)
        self._thread.start()
        label = self.title or self.url or f"win={self.win} tab={self.tab}"
        return f"watching: {label!r}  (every {self.interval}s)"

    def stop(self) -> str:
        self._stop.set()
        return "tab watch stopped"

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    # ── background loop ───────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._poll()

    def _poll(self) -> None:
        try:
            content = read_tab_text(self.win, self.tab)
            if content == self._last_content:
                return
            diff = self._diff(self._last_content, content)
            self._last_content = content
            self._report(content, diff)
        except Exception as e:
            print(f"[browser.watch] poll error: {e}", flush=True)

    # ── helpers ───────────────────────────────────────────────────

    def _diff(self, old: str, new: str) -> str:
        old_lines = old.splitlines()
        new_lines = new.splitlines()
        lines = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
        return "\n".join(lines[:150])

    def _report(self, content: str, diff: str) -> None:
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            payload = {
                "title": self.title,
                "url": self.url,
                "snapshot": content[:6000],
                "diff": diff[:2500],
            }
            r = httpx.post(
                f"{self.api_base}/v1/browser/tab-event",
                json=payload,
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            summary = r.json().get("summary", "")
            if summary:
                self._notify(summary[:250])
                print(
                    f"\n[tab update] {self.title or self.url}\n  {summary}\n",
                    flush=True,
                )
        except Exception as e:
            print(f"[browser.watch] report error: {e}", flush=True)

    def _notify(self, text: str) -> None:
        script = (
            f"display notification {json.dumps(text)} "
            f'with title "Scrappy — tab update"'
        )
        subprocess.run(["osascript", "-e", script], check=False)
