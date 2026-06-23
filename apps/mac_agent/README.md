# Vault Zeta — Mac Voice Agent

Push-to-talk client that turns your MacBook into Scrappy Singh's voice interface.

## How it works

```
mic → /v1/voice/transcribe (Whisper)
     → /v1/chat            (Gemma, SSE stream)
     → sentence-buffered → /v1/voice/speak (ElevenLabs)
     → afplay → speakers
```

Streaming tokens are split on sentence boundaries (`.!?`), each completed sentence
is sent to ElevenLabs immediately, and the audio chunks queue into `afplay` so you
hear Scrappy start talking back while he's still generating later sentences.

Session ID is persisted to `~/.vault_zeta_session` so memory carries across runs.

## Run

In one terminal — the API server:

```bash
cd ~/Documents/itsmay
source .venv/bin/activate
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

In a second terminal — the voice agent:

```bash
cd ~/Documents/itsmay
source .venv/bin/activate
python -m apps.mac_agent.voice_loop
```

Press **ENTER** to start recording. Press **ENTER** again to stop and send.
Ctrl+C to quit.

First run: macOS will ask for microphone permission for Terminal (or iTerm /
whichever app you launched from). Grant it.

## Config (env)

| var | default | what |
|---|---|---|
| `VAULT_API_BASE` | `http://127.0.0.1:8000` | where the FastAPI server lives |

If you run the server on a different machine (e.g. Linux server over Tailscale),
export `VAULT_API_BASE=http://srv:8000` before launching.

## Knobs

Inside `voice_loop.py`:
- `SAMPLE_RATE = 16000` — Whisper-native; don't change without good reason
- `MIN_RECORDING_SEC = 0.3` — anything shorter is dropped as a misfire
- `SESSION_FILE` — where the rolling session UUID lives (`~/.vault_zeta_session`)

To start a fresh conversation, delete the session file:
```bash
rm ~/.vault_zeta_session
```

## Latency expectations on M-series

| step | cold | warm |
|---|---|---|
| Mic capture | (your speech length) | same |
| Whisper STT (small.en) | ~45s first call (model download) | <1s for short utterances |
| Gemma 3 first token | ~25s first call (model warmup) | ~300ms |
| ElevenLabs first audio | ~500ms per sentence | same |
| afplay startup | ~50ms per chunk | same |

After the first warm-up turn, expect total round-trip ≈ 1–2s for short
exchanges before you hear the first audible word.

## Known gotchas

- **Push-to-talk via ENTER blocks the terminal.** That's fine for now. A real
  hotkey daemon (spacebar global hotkey) is a future upgrade.
- **mp3 playback uses `afplay`** — macOS-native, zero extra deps, ~50ms startup
  per file. For lower latency switch ElevenLabs `output_format` to a raw PCM
  format and pipe through `sounddevice`. Future slice.
- **No barge-in.** If you start talking while Scrappy is speaking, the audio
  queue won't pause. Future slice.
