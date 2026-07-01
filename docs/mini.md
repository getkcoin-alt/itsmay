# Mini AI — the fully-local voice companion

A sovereign "best friend" that runs **entirely on local open-source AI** (no cloud).
It recognizes each person by **voiceprint**, keeps a **fresh memory per person**,
**observes quietly** and only speaks when addressed, talks in a **childish,
genderless** voice, and is given a **nickname** by each user. One process per
device, one (or a few) people per device.

Built as a new module inside itsmay, reusing the local stack (it does *not* touch
Scrappy/the operator API).

## Personalities (pick one per person)

Each person chooses their companion's vibe at enrollment (stored on the profile,
default `COMPANION_PERSONA`). Adding a third is just a prompt file + a registry
entry in `core/companion/persona.py`.

- **`friend`** — playful, childish, gentle roasting; a warm peer who's just glad
  you showed up (`system_mini.md`).
- **`mentor`** — an adaptive lifelong companion: exceptional memory, calm
  confidence, honest feedback, dry humor, and a relentless focus on helping you
  become who you're trying to be (`system_mini_mentor.md`).

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

mini enroll        # capture a voice, give the bot a nickname + pick a personality
mini run           # live chat — replies as you talk
mini personas      # the personalities you can pick
mini profiles      # who's enrolled (+ nickname + personality each chose)
mini memories <id> # what it remembers about someone
mini forget <id>   # erase a person entirely
mini reset         # wipe everyone and start fresh
```

## Live controls during `mini run`

| Key | Does |
|---|---|
| **O** | Toggle **observe** mode — it just listens + remembers, says nothing. Press again to talk. |
| **V** | Cycle the **voice** (Piper / ElevenLabs / macOS say) without restarting. |
| **Ctrl-C** | Quit. |

By default it **replies to everything you say** (a live conversation); press **O**
when you want it to fall silent and just remember.

## Voice options (`COMPANION_VOICE`, or press V)

- **`auto`** (default) — Piper if `PIPER_VOICE_PATH` is set, else macOS `say`. Fully local.
- **`elevenlabs`** — expressive, emotional cloud voice (needs `ELEVENLABS_API_KEY`);
  pick an expressive `ELEVENLABS_VOICE_ID`. The brain + memory stay **local** — only
  the voice is cloud. You can **interrupt it** mid-sentence (barge-in).
- **`say`** / **`piper`** — force one explicitly.

## Status

- ✅ **CI-tested:** profile store, voiceprint matching, when-to-talk gate +
  observe/talk mode override, persona registry, voice selection, ElevenLabs PCM,
  local-TTS streaming, and the full engine (talk/observe/follow-up + memory) over
  real SQLite with fakes. Also: concurrent-write safety (busy_timeout), the
  per-profile session lock (no double sessions), vectorized memory search, the
  observation guard (short/duplicate skips), and the unknown-voice hint throttle.
- ⏳ **Mac-validate next (needs a mic + the models):** the live `mini run`/`enroll`
  loop — real Ollama reply, faster-whisper STT, Piper voice, Resemblyzer voiceprint
  recognition across two real speakers. Pull the network to prove it's offline.

## First run & unknown voices
- **First run** (`mini run` with nobody enrolled) walks you through enrollment
  inline, then starts listening — no separate `mini enroll` step needed.
- **Unknown mid-run voices** are never folded into a known person's memory
  (no cross-contamination). Instead you get a throttled hint to run `mini enroll`
  so the new person gets their own profile + memory.

## Known edges / next
- Inline "want me to remember you?" auto-enrollment *mid-conversation* (vs. the
  first-run flow above) is a follow-up.
- Barge-in during the bot's reply, sentence-streamed TTS (lower latency), and a
  true pitch-shifted child voice are polish items.
- Heavier-than-Pi deps (torch). A lighter ONNX speaker model is the path to
  Raspberry Pi / Jetson later.
- Proactively answering un-addressed questions is deliberately **off** in v1.
