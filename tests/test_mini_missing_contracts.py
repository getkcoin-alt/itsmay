from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from core.companion import tenants
from core.companion.emotion_stream import EmotionResponseParser
from core.companion.voice_features import EMBED_DIM, embed_pcm


# ── emotion streaming ─────────────────────────────────────────────


def test_emotion_parser_streams_only_response_text_across_arbitrary_chunks():
    parser = EmotionResponseParser()
    chunks = [
        '{"emo',
        'tion":"hap',
        'py","res',
        'ponse":"hello ',
        'there","meta":true}',
    ]
    emotions: list[str] = []
    spoken = ""
    for chunk in chunks:
        emotion, text = parser.feed(chunk)
        if emotion:
            emotions.append(emotion)
        spoken += text
    spoken += parser.flush()

    assert emotions == ["happy"]
    assert parser.emotion == "happy"
    assert spoken == "hello there"
    assert "emotion" not in spoken
    assert "meta" not in spoken
    assert "{" not in spoken


def test_emotion_parser_decodes_json_escapes_without_speaking_scaffolding():
    parser = EmotionResponseParser()
    emotion, first = parser.feed(
        '{"emotion":"excited","response":"line one\\nhe said \\"hi\\" and smile \\u263A"}'
    )
    tail = parser.flush()

    assert emotion == "excited"
    assert first + tail == 'line one\nhe said "hi" and smile ☺'


def test_emotion_parser_maps_unknown_emotion_to_neutral():
    parser = EmotionResponseParser()
    emotion, spoken = parser.feed('{"emotion":"furious","response":"safe text"}')
    assert emotion == "neutral"
    assert parser.emotion == "neutral"
    assert spoken == "safe text"


def test_emotion_parser_malformed_json_never_falls_back_to_raw_json_speech():
    parser = EmotionResponseParser()
    emotion, spoken = parser.feed('{"emotion":"happy","oops":"not response"}')
    assert emotion == "happy"
    assert spoken == ""
    assert parser.flush() == ""


# ── browser voice features ────────────────────────────────────────


def _tone(freq: float, *, sr: int = 16_000, seconds: float = 0.8) -> np.ndarray:
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    wav = 0.35 * np.sin(2 * np.pi * freq * t)
    return np.asarray(wav * 32767.0, dtype=np.int16)


def test_voice_features_are_fixed_length_normalized_and_deterministic():
    pcm = _tone(180.0)
    first = embed_pcm(pcm, 16_000)
    second = embed_pcm(pcm.copy(), 16_000)

    assert first.shape == (EMBED_DIM,)
    assert first.dtype == np.float32
    assert np.isclose(np.linalg.norm(first), 1.0, atol=1e-5)
    assert np.allclose(first, second)


def test_voice_features_fail_closed_for_silence_short_audio_and_bad_rate():
    assert not np.any(embed_pcm(np.zeros(16_000, dtype=np.int16), 16_000))
    assert not np.any(embed_pcm(_tone(180.0, seconds=0.1), 16_000))
    assert not np.any(embed_pcm(_tone(180.0), 1_000))


def test_voice_features_resample_to_same_contract_dimension():
    at_8k = embed_pcm(_tone(220.0, sr=8_000), 8_000)
    at_16k = embed_pcm(_tone(220.0, sr=16_000), 16_000)
    assert at_8k.shape == at_16k.shape == (EMBED_DIM,)
    assert np.any(at_8k) and np.any(at_16k)


@pytest.fixture
def tenant_env(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        tenants_db_path=str(tmp_path / "registry" / "tenants.db"),
        tenants_data_dir=str(tmp_path / "tenant-data"),
    )
    monkeypatch.setattr(tenants, "get_settings", lambda: settings)
    tenants._failures.clear()
    tenants._locked_until.clear()
    return settings


def test_tenant_creation_returns_pin_once_but_registry_stores_only_hash(tenant_env):
    tenant, pin = tenants.create_tenant("Priya", pin="4821")

    assert pin == "4821"
    assert tenant.owner_name == "Priya"
    assert tenants.verify_pin(tenant, "4821") is True
    assert tenants.verify_pin(tenant, "4822") is False
    assert tenant.pin_hash != b"4821"
    assert tenant.pin_salt

    with sqlite3.connect(tenant_env.tenants_db_path) as conn:
        row = conn.execute(
            "SELECT pin_salt, pin_hash FROM mini_tenants WHERE slug = ?", (tenant.slug,)
        ).fetchone()
    assert row is not None
    assert b"4821" not in bytes(row[0])
    assert b"4821" not in bytes(row[1])


def test_two_tenants_get_separate_slugs_and_database_paths(tenant_env):
    first, _ = tenants.create_tenant("A", pin="1111")
    second, _ = tenants.create_tenant("B", pin="2222")

    assert first.slug != second.slug
    assert first.db_path != second.db_path
    assert tenants.get_tenant_by_slug(first.slug) == first
    assert [item.slug for item in tenants.list_tenants()] == [first.slug, second.slug]


def test_wrong_pin_throttles_and_does_not_unlock_with_correct_pin_until_window(tenant_env):
    tenant, _ = tenants.create_tenant("A", pin="1234")
    for _ in range(5):
        assert tenants.verify_pin(tenant, "9999") is False
    assert tenants.verify_pin(tenant, "1234") is False


def test_delete_tenant_removes_only_registered_isolated_database(tenant_env):
    tenant, _ = tenants.create_tenant("A", pin="1234")
    db_path = tenants._safe_registered_db_path(tenant)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"tenant database placeholder")
    outside = db_path.parent.parent / "do-not-delete.txt"
    outside.write_text("keep", encoding="utf-8")

    assert tenants.delete_tenant(tenant.slug) is True
    assert not db_path.exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert tenants.get_tenant_by_slug(tenant.slug) is None
    assert tenants.delete_tenant(tenant.slug) is False


def test_tampered_registry_path_cannot_be_used_as_arbitrary_delete(tenant_env, tmp_path):
    tenant, _ = tenants.create_tenant("A", pin="1234")
    outside = tmp_path / "valuable.txt"
    outside.write_text("keep", encoding="utf-8")

    with sqlite3.connect(tenant_env.tenants_db_path) as conn:
        conn.execute(
            "UPDATE mini_tenants SET db_path = ? WHERE slug = ?",
            (str(outside), tenant.slug),
        )
        conn.commit()

    with pytest.raises(ValueError, match="outside configured data directory"):
        tenants.delete_tenant(tenant.slug)
    assert outside.read_text(encoding="utf-8") == "keep"
