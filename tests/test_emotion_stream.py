from __future__ import annotations

from core.companion.emotion_stream import EmotionResponseParser


def test_emotion_and_response_stream_across_arbitrary_chunks() -> None:
    parser = EmotionResponseParser()
    chunks = [
        '{"emo',
        'tion":"hap',
        'py","response":"Hel',
        'lo, ',
        'world!"}',
    ]

    emotions: list[str] = []
    spoken: list[str] = []
    for chunk in chunks:
        emotion, text = parser.feed(chunk)
        if emotion:
            emotions.append(emotion)
        spoken.append(text)

    spoken.append(parser.flush())
    assert emotions == ["happy"]
    assert parser.emotion == "happy"
    assert "".join(spoken) == "Hello, world!"


def test_json_escapes_can_span_chunks() -> None:
    parser = EmotionResponseParser()
    chunks = [
        '{"emotion":"excited","response":"Line one\\',
        'nLine two: \\u2',
        '764 and quote: \\"ok\\""}',
    ]

    spoken = ""
    for chunk in chunks:
        _, delta = parser.feed(chunk)
        spoken += delta
    spoken += parser.flush()

    assert spoken == 'Line one\nLine two: ❤ and quote: "ok"'


def test_non_json_output_falls_back_instead_of_silence() -> None:
    parser = EmotionResponseParser()
    parser.feed("Hello from a model that ignored the JSON contract.")

    assert parser.flush() == "Hello from a model that ignored the JSON contract."
    assert parser.flush() == ""


def test_complete_json_with_response_is_not_repeated_on_flush() -> None:
    parser = EmotionResponseParser()
    emotion, spoken = parser.feed('{"emotion":"neutral","response":"All good."}')

    assert emotion == "neutral"
    assert spoken == "All good."
    assert parser.flush() == ""
