"""Mini AI — a fully-local voice companion ("best friend") built on the itsmay stack.

Distinct from Scrappy (the operator): Mini AI runs entirely on local open-source
models (Ollama LLM, faster-whisper STT, local TTS, fastembed memory), forms a
per-person friendship keyed to the speaker's voiceprint, observes quietly and
remembers, and only speaks when addressed. One process per device, no cloud.

Modules:
    profiles   — per-person profiles (voiceprint ↔ memory namespace ↔ nickname)
    speaker_id — enroll/recognize a voice (which profile is talking?)
    gate       — the "when to talk vs just observe" policy
    persona    — the best-friend system prompt + context assembly
    runtime    — the conversation/observe loop tying it together
"""
