"""`mini voices` — listing/filtering ElevenLabs voices (mocked API)."""

from __future__ import annotations

import httpx

from core.companion.voice import filter_voices, format_voice
from core.voice.tts_elevenlabs import ElevenLabsTTS

_VOICES = [
    {"id": "v1", "name": "River", "description": "calm, grounded",
     "labels": {"gender": "neutral", "age": "young", "accent": "american"}},
    {"id": "v2", "name": "Rachel", "description": "mature narrator",
     "labels": {"gender": "female", "age": "middle-aged"}},
]


def test_filter_matches_name_labels_description():
    assert [v["id"] for v in filter_voices(_VOICES, "neutral")] == ["v1"]  # label
    assert [v["id"] for v in filter_voices(_VOICES, "narrator")] == ["v2"]  # description
    assert [v["id"] for v in filter_voices(_VOICES, "rachel")] == ["v2"]  # name
    assert len(filter_voices(_VOICES, "")) == 2  # empty → all
    assert filter_voices(_VOICES, "zzz") == []


def test_format_voice_builds_tag_line():
    name, vid, tags = format_voice(_VOICES[0])
    assert name == "River" and vid == "v1"
    assert "neutral" in tags and "young" in tags and "american" in tags


async def test_list_voices_parses_api(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == "sk-test"
        return httpx.Response(
            200,
            json={"voices": [
                {"voice_id": "abc", "name": "River",
                 "labels": {"gender": "neutral"}, "description": "calm", "category": "premade"}
            ]},
        )

    tts = ElevenLabsTTS(api_key="sk-test")
    tts._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    voices = await tts.list_voices()
    await tts.aclose()
    assert voices == [
        {"id": "abc", "name": "River", "labels": {"gender": "neutral"},
         "description": "calm", "category": "premade"}
    ]


async def test_list_voices_requires_key():
    tts = ElevenLabsTTS(api_key="")
    try:
        raised = False
        try:
            await tts.list_voices()
        except RuntimeError:
            raised = True
        assert raised
    finally:
        await tts.aclose()
