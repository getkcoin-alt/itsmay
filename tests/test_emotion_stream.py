from __future__ import annotations

from core.companion.emotion_stream import EmotionResponseParser


def test_stream_parser_emits_emotion_then_spoken_text():
    parser = EmotionResponseParser()

    emo, spoken = parser.feed('{"emotion":"happy","response":"Hel')
    assert emo == "happy"
    assert spoken == "Hel"

    emo, spoken = parser.feed('lo world"}')
    assert emo is None
    assert spoken == "lo world"
    assert parser.flush() == ""
    assert parser.emotion == "happy"


def test_stream_parser_handles_key_split_across_chunks():
    parser = EmotionResponseParser()
    chunks = [
        '{"emo',
        'tion": "thinking", "res',
        'ponse": "BOYI, ',
        'I am here."}',
    ]

    emotions: list[str] = []
    spoken: list[str] = []
    for chunk in chunks:
        emo, text = parser.feed(chunk)
        if emo:
            emotions.append(emo)
        spoken.append(text)

    assert emotions == ["thinking"]
    assert "".join(spoken) == "BOYI, I am here."


def test_stream_parser_decodes_json_escapes_incrementally():
    parser = EmotionResponseParser()
    parts = [
        '{"emotion":"excited","response":"line 1\\nquote: \\',
        '"yes\\" and unicode \\u2',
        '764"}',
    ]

    out: list[str] = []
    for part in parts:
        _, text = parser.feed(part)
        out.append(text)

    assert "".join(out) == 'line 1\nquote: "yes" and unicode ❤'


def test_stream_parser_ignores_extra_keys_after_response():
    parser = EmotionResponseParser()
    _, spoken = parser.feed(
        '{"emotion":"neutral","response":"hello","user_name":"Karnveer"}'
    )
    assert spoken == "hello"
    assert parser.flush() == ""


def test_stream_parser_does_not_invent_malformed_response():
    parser = EmotionResponseParser()
    parser.feed('{"emotion":"happy","response":')
    assert parser.flush() == ""
