"""Claude Code stream-json → speakable milestones (pure parser)."""

from __future__ import annotations

import json

from core.connectors.coder.stream import ParsedEvent, parse_stream_line


def _assistant(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool(name: str, **inp) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


def test_non_json_and_blank_lines_are_silent():
    assert parse_stream_line("") == ParsedEvent()
    assert parse_stream_line("not json at all") == ParsedEvent()
    assert parse_stream_line("{broken json") == ParsedEvent()
    assert parse_stream_line("[1, 2, 3]") == ParsedEvent()  # JSON but not a dict


def test_system_init_event_is_silent():
    line = json.dumps({"type": "system", "subtype": "init", "tools": ["Write"]})
    assert parse_stream_line(line) == ParsedEvent()


def test_assistant_text_becomes_a_thinking_milestone():
    ev = parse_stream_line(_assistant(_text("I'll build a calculator with HTML and JS.")))
    assert ev.milestones == ["I'll build a calculator with HTML and JS."]
    assert ev.final_text is None


def test_assistant_text_strips_the_scrappy_result_line():
    # The final message carries both a human sentence and the machine line; we
    # narrate the sentence and never speak the raw JSON.
    txt = 'Done — it works.\nSCRAPPY_RESULT: {"ok": true, "open": "index.html"}'
    ev = parse_stream_line(_assistant(_text(txt)))
    assert ev.milestones == ["Done — it works."]


def test_assistant_text_that_is_only_result_line_is_silent():
    ev = parse_stream_line(_assistant(_text('SCRAPPY_RESULT: {"ok": true, "open": ""}')))
    assert ev.milestones == []


def test_write_and_edit_name_the_file():
    assert parse_stream_line(
        _assistant(_tool("Write", file_path="/tmp/proj/index.html"))
    ).milestones == ["Writing index.html"]
    assert parse_stream_line(
        _assistant(_tool("Edit", file_path="app.py"))
    ).milestones == ["Editing app.py"]


def test_bash_prefers_description_then_command():
    assert parse_stream_line(
        _assistant(_tool("Bash", command="npm install", description="Install dependencies"))
    ).milestones == ["Install dependencies"]
    assert parse_stream_line(
        _assistant(_tool("Bash", command="npm   install"))
    ).milestones == ["Running: npm install"]


def test_low_signal_tools_are_skipped():
    for name in ("Read", "Glob", "Grep", "LS", "TodoWrite"):
        assert parse_stream_line(_assistant(_tool(name, file_path="x"))).milestones == []


def test_multiple_blocks_preserve_order():
    ev = parse_stream_line(
        _assistant(
            _text("Setting up the project."),
            _tool("Write", file_path="index.html"),
            _tool("Read", file_path="ignored.txt"),  # skipped, no gap
            _tool("Bash", command="python -m http.server", description="Serve it"),
        )
    )
    assert ev.milestones == ["Setting up the project.", "Writing index.html", "Serve it"]


def test_result_event_yields_final_text():
    line = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": 'All set.\nSCRAPPY_RESULT: {"ok": true, "open": "index.html"}',
            "total_cost_usd": 0.01,
        }
    )
    ev = parse_stream_line(line)
    assert ev.milestones == []
    assert ev.final_text is not None
    assert "SCRAPPY_RESULT" in ev.final_text  # worker relays this for parse_result


def test_long_milestone_is_clipped():
    ev = parse_stream_line(_assistant(_text("word " * 100)))
    assert len(ev.milestones) == 1
    assert ev.milestones[0].endswith("…")
    assert len(ev.milestones[0]) <= 161
