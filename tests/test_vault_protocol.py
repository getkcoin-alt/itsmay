"""Vault Zeta — the portable continuity protocol.

The properties here are what make a bundle safe to move between hosts, runtimes
and models. Each test pins one of the protocol's normative rules.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np
import pytest

from core.vault.bundle import MalformedVault, VaultBundle
from core.vault.export import _trust_for, build_bundle, build_identity
from core.vault.import_ import ImportReport, import_bundle, resolve_identity
from core.vault.redact import SecretInVault, find_secret, mask
from core.vault.schema import (
    PROTOCOL_VERSION,
    Directive,
    Identity,
    IncompatibleVault,
    Manifest,
    MemoryRecord,
    Operator,
    VaultState,
    check_compatible,
    content_hash,
)

USER = UUID("11111111-1111-1111-1111-111111111111")


# ── fakes ─────────────────────────────────────────────────────────────


class _Row:
    def __init__(self, content, *, kind="factual", source="coder.build", importance=0.7):
        self.id = uuid4()
        self.kind = kind
        self.content = content
        self.source = source
        self.importance = importance
        self.created_at = datetime(2026, 8, 1, tzinfo=UTC)
        self.last_used_at = None
        self.use_count = 0


class FakeSemantic:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.written: list[dict] = []

    async def list_recent(self, user_id, *, limit=50, offset=0, kind=None):
        return self.rows[offset : offset + limit]

    async def count(self, user_id):
        return len(self.rows)

    async def write(self, user_id, kind, content, embedding, *, source="", importance=0.5):
        self.written.append(
            {"kind": kind, "content": content, "source": source, "importance": importance}
        )
        self.rows.append(_Row(content, kind=kind, source=source, importance=importance))
        return uuid4()


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    async def embed(self, text: str):
        self.calls += 1
        return np.ones(4, dtype=np.float32)


class FakeEpisodic:
    pass  # no list_recent_messages → episodes are skipped, by design


async def _bundle(rows, **kw) -> VaultBundle:
    return await build_bundle(
        semantic=FakeSemantic(rows), episodic=FakeEpisodic(), user_id=USER, **kw
    )


# ── schema + versioning ───────────────────────────────────────────────


def test_content_hash_normalises_whitespace():
    # The same fact wrapped differently must dedupe to one record.
    assert content_hash("a  b\nc") == content_hash("a b c")
    assert content_hash("a b") != content_hash("a c")


def test_check_compatible_accepts_same_major_refuses_next():
    check_compatible(PROTOCOL_VERSION)
    check_compatible("1.9.3")  # newer minor is fine — unknown fields are kept
    with pytest.raises(IncompatibleVault):
        check_compatible("2.0.0")
    with pytest.raises(IncompatibleVault):
        check_compatible("banana")


def test_unknown_fields_survive_a_round_trip():
    # THE forward-compatibility rule: an older host must not destroy a newer
    # host's data just because it doesn't understand a field.
    raw = {
        "id": "m1",
        "kind": "factual",
        "content": "hi",
        "future_field": {"added": "in v1.4"},
    }
    record = MemoryRecord.model_validate(raw)
    dumped = record.model_dump(mode="json")
    assert dumped["future_field"] == {"added": "in v1.4"}


def test_x_extension_point_round_trips():
    record = MemoryRecord(id="m1", kind="factual", content="hi", x={"host": "mac"})
    assert MemoryRecord.model_validate(record.model_dump()).x == {"host": "mac"}


# ── redaction gate ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "deploy with sk-live-abcdefghijklmnopqrstuvwxyz",
        "token ghp_abcdefghijklmnopqrstuvwxyz012345",
        "AKIAIOSFODNN7EXAMPLE is the key",
        "-----BEGIN RSA PRIVATE KEY-----",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123",
        "postgres://user:hunter2pass@db.host/scrappy",
    ],
)
def test_find_secret_catches_credential_shapes(text):
    assert find_secret(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "built a countdown timer app",
        "Karnveer prefers direct answers",
        "the disk is at 92.3% used",
        "",
    ],
)
def test_find_secret_leaves_ordinary_prose_alone(text):
    assert find_secret(text) is None


async def test_export_refuses_a_bundle_containing_a_secret():
    # Refuse, don't mask: a vault that silently rewrote itself would be worse.
    rows = [_Row("the key is sk-live-abcdefghijklmnopqrstuvwx")]
    with pytest.raises(SecretInVault) as e:
        await _bundle(rows)
    assert "refusing to export" in str(e.value)
    assert "openai-style key" in str(e.value)


def test_mask_is_available_for_log_lines():
    assert "sk-live" not in mask("key sk-live-abcdefghijklmnopqrstuv here")
    assert "[REDACTED]" in mask("key sk-live-abcdefghijklmnopqrstuv here")


# ── export ────────────────────────────────────────────────────────────


async def test_export_carries_content_and_provenance_but_no_vectors():
    bundle = await _bundle([_Row("built a ctimer app")])
    assert len(bundle.memories) == 1
    m = bundle.memories[0]
    assert m.content == "built a ctimer app"
    assert m.content_sha256 == content_hash(m.content)
    assert m.source == "coder.build"
    assert m.learned_by  # which host learned it
    assert m.trust == "tool"  # coder.* output is the likeliest injection carrier

    # No embedding anywhere in the serialized bundle — the portability rule.
    blob = json.dumps(m.model_dump(mode="json"))
    assert "embedding" not in blob
    assert not any(isinstance(v, list) and v and isinstance(v[0], float)
                   for v in m.model_dump().values())


def test_trust_classification():
    assert _trust_for("coder.build") == "tool"
    assert _trust_for("web.fetch") == "tool"
    assert _trust_for("agent:memory_keeper") == "operator"
    assert _trust_for("seed:knowledge.yaml") == "operator"
    assert _trust_for("consolidator") == "derived"
    assert _trust_for(None) == "derived"


def test_identity_carries_secret_names_never_values():
    identity = build_identity()
    assert identity.name == "Scrappy Singh"
    assert identity.operator.address_as == "Boss"
    assert identity.persona and identity.invariants
    # Names only — a value in here would leave the machine.
    assert "elevenlabs_api_key" in identity.secret_refs
    for ref in identity.secret_refs:
        assert find_secret(ref) is None


async def test_export_omits_episodes_when_asked():
    bundle = await _bundle([_Row("x")], include_episodes=False)
    assert bundle.episodes == []
    assert bundle.manifest.includes_episodes is False


# ── bundle on disk ────────────────────────────────────────────────────


async def test_write_then_read_round_trips(tmp_path):
    original = await _bundle([_Row("built a ctimer app"), _Row("Boss prefers brevity")])
    original.write(tmp_path / "vault")
    loaded = VaultBundle.read(tmp_path / "vault")

    assert loaded.manifest.protocol_version == PROTOCOL_VERSION
    assert loaded.identity.name == original.identity.name
    assert [m.content for m in loaded.memories] == [m.content for m in original.memories]
    assert loaded.manifest.counts["memories"] == 2
    assert len(loaded.directives) == len(original.directives)


async def test_written_bundle_is_plain_readable_files(tmp_path):
    # A protocol another language can implement — so the files must be ordinary.
    (await _bundle([_Row("hi")])).write(tmp_path / "v")
    names = {p.name for p in (tmp_path / "v").iterdir()}
    assert {"manifest.json", "identity.json", "memories.jsonl"} <= names
    line = (tmp_path / "v" / "memories.jsonl").read_text().splitlines()[0]
    assert json.loads(line)["content"] == "hi"  # one JSON object per line


def test_read_refuses_a_future_major(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    manifest = Manifest(protocol_version="2.0.0", vault_id="x")
    (d / "manifest.json").write_text(json.dumps(manifest.model_dump(mode="json")))
    with pytest.raises(IncompatibleVault):
        VaultBundle.read(d)


def test_read_reports_a_malformed_bundle(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(MalformedVault):
        VaultBundle.read(tmp_path / "empty")
    with pytest.raises(MalformedVault):
        VaultBundle.read(tmp_path / "does-not-exist")


def test_one_corrupt_line_does_not_lose_the_rest(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    VaultBundle(
        manifest=Manifest(vault_id="x"),
        identity=Identity(name="S", operator=Operator(handle="k")),
        memories=[MemoryRecord(id="a", kind="factual", content="one")],
        state=VaultState(),
    ).write(d)
    with (d / "memories.jsonl").open("a") as fh:
        fh.write("{ not json\n")
        fh.write(json.dumps({"id": "b", "kind": "factual", "content": "two"}) + "\n")

    loaded = VaultBundle.read(d)
    assert [m.content for m in loaded.memories] == ["one", "two"]  # bad line skipped


# ── import ────────────────────────────────────────────────────────────


async def test_import_re_embeds_on_this_host():
    # The portability payoff: vectors are derived here, not carried.
    bundle = await _bundle([_Row("built a ctimer app")])
    target, embedder = FakeSemantic(), FakeEmbedder()
    report = await import_bundle(
        bundle, semantic=target, embedder=embedder, user_id=USER
    )
    assert report.memories_added == 1
    assert embedder.calls == 1
    assert target.written[0]["content"] == "built a ctimer app"


async def test_import_dedupes_by_content_not_id():
    # Same fact re-exported from another host has a different id — still one fact.
    bundle = await _bundle([_Row("built a ctimer app")])
    target = FakeSemantic([_Row("built a ctimer app", source="local")])
    report = await import_bundle(
        bundle, semantic=target, embedder=FakeEmbedder(), user_id=USER
    )
    assert report.memories_added == 0
    assert report.memories_skipped == 1


async def test_import_keeps_provenance_across_the_hop():
    bundle = await _bundle([_Row("a fact")])
    target = FakeSemantic()
    await import_bundle(bundle, semantic=target, embedder=FakeEmbedder(), user_id=USER)
    # Must not later claim this host learned it first.
    assert target.written[0]["source"].startswith("vault:")


async def test_import_dry_run_writes_nothing():
    bundle = await _bundle([_Row("a fact")])
    target, embedder = FakeSemantic(), FakeEmbedder()
    report = await import_bundle(
        bundle, semantic=target, embedder=embedder, user_id=USER, dry_run=True
    )
    assert report.memories_added == 1  # what WOULD happen
    assert target.written == [] and embedder.calls == 0


async def test_import_survives_a_failing_embedder():
    class Broken(FakeEmbedder):
        async def embed(self, text):
            raise RuntimeError("model down")

    bundle = await _bundle([_Row("a"), _Row("b")])
    report = await import_bundle(
        bundle, semantic=FakeSemantic(), embedder=Broken(), user_id=USER
    )
    assert report.memories_failed == 2 and report.memories_added == 0


async def test_full_round_trip_through_disk(tmp_path):
    """export → disk → import into an empty host → the memory is there."""
    source_rows = [_Row("built a ctimer app"), _Row("Boss prefers short answers")]
    (await _bundle(source_rows)).write(tmp_path / "vault")

    loaded = VaultBundle.read(tmp_path / "vault")
    target = FakeSemantic()
    report = await import_bundle(
        loaded, semantic=target, embedder=FakeEmbedder(), user_id=USER
    )
    assert report.memories_added == 2
    assert {w["content"] for w in target.written} == {
        "built a ctimer app",
        "Boss prefers short answers",
    }
    # Importing the same bundle again changes nothing.
    again = await import_bundle(
        loaded, semantic=target, embedder=FakeEmbedder(), user_id=USER
    )
    assert again.memories_added == 0 and again.memories_skipped == 2


# ── identity merge ────────────────────────────────────────────────────


def _identity(revision: int, name: str = "Scrappy Singh") -> Identity:
    return Identity(name=name, operator=Operator(handle="karnveer"), revision=revision)


def test_identity_higher_revision_wins():
    chosen, changed, why = resolve_identity(_identity(3), _identity(2))
    assert chosen.revision == 3 and changed is True
    assert "revision 3" in why and "local 2" in why


def test_identity_lower_revision_is_refused():
    chosen, changed, _ = resolve_identity(_identity(1), _identity(5))
    assert chosen.revision == 5 and changed is False


def test_identity_tie_keeps_local_so_hosts_dont_ping_pong():
    local = _identity(4, name="Local")
    chosen, changed, why = resolve_identity(_identity(4, name="Incoming"), local)
    assert chosen.name == "Local" and changed is False and "same revision" in why


def test_identity_adopted_when_host_has_none():
    chosen, changed, why = resolve_identity(_identity(2), None)
    assert changed is True and chosen.revision == 2 and "no local identity" in why


def test_import_report_is_reportable():
    r = ImportReport(memories_added=3, memories_skipped=1)
    assert "3 added" in r.summary() and "1 already known" in r.summary()
    assert r.to_dict()["memories_added"] == 3


def test_directive_hash_is_populated():
    d = Directive(id="d1", content="Be direct.", content_sha256=content_hash("Be direct."))
    assert d.content_sha256 == content_hash("Be direct.")
