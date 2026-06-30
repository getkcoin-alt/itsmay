# Mini AI — the fully-local voice companion

A sovereign "best friend" that runs **entirely on local open-source AI** (no cloud).
It recognizes each person by **voiceprint**, keeps a **fresh memory per person**,
**observes quietly** and only speaks when addressed, talks in a **childish,
genderless** voice, and is given a **nickname** by each user. One process per
device, one (or a few) people per device.

Built as a new module inside itsmay, reusing the local stack (it does *not* touch
Scrappy/the operator API).

## What runs where

| Layer | Local engine |
|---|---|
| Brain | **Ollama** instruct model (`COMPANION_MODEL`, default `llama3.2:3b`) |
| Speech-to-text | **faster-whisper** (`STT_PROVIDER=local`) |
| Voice (TTS) | **Piper** (`PIPER_VOICE_PATH`) — childish/neutral voice |
| Voiceprint ID | **Resemblyzer** speaker embeddings, cosine match |
| Memory | local **SQLite** + fastembed (one file: `COMPANION_SQLITE_PATH`) |
| Mic / VAD | `sounddevice` + the shared WebRTC/energy VAD |

Nothing leaves the machine. Forgetting a person erases their voice + memories.

## Architecture

`mic → VAD → local STT → speaker-ID (which profile?) → engine → local TTS → speaker`

The brains live in `core/companion/`, all **audio-free and unit-tested**:
- `profiles.py` — `voice_profiles` table: voiceprint ↔ memory namespace ↔ nickname.
- `speaker_id.py` — `best_match` (pure cosine matcher) + a lazy Resemblyzer encoder.
- `gate.py` — the **when-to-talk** policy (addressed / follow-up / observe).
- `persona.py` + `core/brain/prompts/system_mini.md` — the best-friend prompt.
- `runtime.py` — `CompanionEngine`: identify → gate → recall → generate → remember.

The mic/speaker loop is `apps/mini/loop.py` (device-only); the engine is what's
tested in CI with fakes (no Ollama/torch/audio).

## Commands

```bash
pip install -e ".[mini]"        # one-time (pulls torch via resemblyzer)
# point at local models:
export COMPANION_MODEL=llama3.2:3b          # ollama pull llama3.2:3b
export PIPER_VOICE_PATH=~/piper/voice.onnx  # a youthful Piper voice
export STT_PROVIDER=local

mini enroll        # capture a voice, give the bot a nickname
mini run           # listen · remember · speak when addressed
mini profiles      # who's enrolled (+ the nickname each gave the bot)
mini memories <id> # what it remembers about someone
mini forget <id>   # erase a person entirely
```

## Status

- ✅ **CI-tested (26 tests):** profile store, voiceprint matching, when-to-talk
  gate, persona assembly, local-TTS streaming, and the full engine
  (talk/observe/follow-up + memory) over real SQLite with fakes.
- ⏳ **Mac-validate next (needs a mic + the models):** the live `mini run`/`enroll`
  loop — real Ollama reply, faster-whisper STT, Piper voice, Resemblyzer voiceprint
  recognition across two real speakers. Pull the network to prove it's offline.

## Known edges / next
- Unknown mid-run voices are skipped (no cross-contamination); inline "want me to
  remember you?" enrollment is a follow-up.
- Barge-in during the bot's reply, sentence-streamed TTS (lower latency), and a
  true pitch-shifted child voice are polish items.
- Heavier-than-Pi deps (torch). A lighter ONNX speaker model is the path to
  Raspberry Pi / Jetson later.
- Proactively answering un-addressed questions is deliberately **off** in v1.
