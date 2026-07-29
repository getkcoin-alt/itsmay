#!/usr/bin/env bash
# It's May — one-command local install. Sovereign: the whole stack runs on your
# own Mac (no Railway, no Postgres, no Docker — memory lives in one SQLite file).
#
#   curl -fsSL https://raw.githubusercontent.com/getkcoin-alt/itsmay/main/scripts/install.sh | bash
#
# Idempotent: safe to re-run. Reads keys from the terminal even when piped.
set -euo pipefail

REPO_URL="${ITSMAY_REPO:-https://github.com/getkcoin-alt/itsmay.git}"
INSTALL_DIR="${ITSMAY_DIR:-$HOME/itsmay}"
CONFIG_DIR="$HOME/.itsmay"
CONFIG_FILE="$CONFIG_DIR/config.env"

say()  { printf '\033[1m▶ %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$1"; }
warn() { printf '\033[33m⚠ %s\033[0m\n' "$1"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# Read a value from the real terminal even when this script is piped from curl.
# We must actually open /dev/tty (a stat-only `[ -r ]` test passes in CI sandboxes
# where the device exists but can't be opened) — so probe it on fd 3 and stay
# silent if there's no usable terminal.
ask() {  # ask "Prompt" VARNAME [silent]
  local prompt="$1" __var="$2" silent="${3:-}" reply=""
  if { exec 3</dev/tty; } 2>/dev/null; then
    if [ "$silent" = "silent" ]; then
      read -r -s -p "$prompt" reply <&3 || reply=""; echo
    else
      read -r -p "$prompt" reply <&3 || reply=""
    fi
    exec 3<&-
  fi
  printf -v "$__var" '%s' "$reply"
}

# ── 1. Python 3.11+ ────────────────────────────────────────────────
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  command -v "$c" >/dev/null 2>&1 || continue
  v=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)
  maj=${v%%.*}; min=${v##*.}
  if [ "$maj" = 3 ] && [ "$min" -ge 11 ]; then PY=$(command -v "$c"); break; fi
done
[ -n "$PY" ] || die "Python 3.11+ not found. Install it with:  brew install python@3.12"
ok "Python $($PY --version 2>&1 | awk '{print $2}')"

# ── 2. Get the code ────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  say "Updating existing checkout at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only || warn "couldn't fast-forward — leaving as-is"
elif [ -f "$PWD/pyproject.toml" ] && grep -q 'name = "vault-zeta"' "$PWD/pyproject.toml" 2>/dev/null; then
  INSTALL_DIR="$PWD"
  say "Installing from current checkout: $INSTALL_DIR"
else
  command -v git >/dev/null 2>&1 || die "git not found. Install Xcode tools:  xcode-select --install"
  say "Cloning into $INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ── 3. venv + install ──────────────────────────────────────────────
[ -d .venv ] || { say "Creating virtualenv"; "$PY" -m venv .venv; }
# shellcheck source=/dev/null
source .venv/bin/activate
say "Installing It's May (first run downloads deps — give it a minute)"
pip install --quiet --upgrade pip
pip install --quiet -e ".[mac]" || die "pip install failed — see the output above"
ok "Installed the 'scrappy' command"

# ── 4. Config + keys ───────────────────────────────────────────────
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_FILE" ]; then
  ok "Config already exists at $CONFIG_FILE (leaving it untouched)"
else
  say "Set up your keys — stored only in $CONFIG_FILE, never committed"
  echo "  Groq powers the brain and voice transcription. Free key:"
  echo "    https://console.groq.com/keys"
  ask "  Paste your Groq API key: " GROQ silent
  ask "  ElevenLabs key for spoken replies (optional — press Enter to skip): " ELEVEN silent
  ( umask 077; cat > "$CONFIG_FILE" <<EOF
# It's May — local config. Real environment variables override these.
LLM_API_KEY=$GROQ
STT_API_KEY=$GROQ
ELEVENLABS_API_KEY=$ELEVEN
MEMORY_BACKEND=auto
SCRAPPY_CLAUDE_FLAGS=--dangerously-skip-permissions
EOF
  )
  chmod 600 "$CONFIG_FILE"
  if [ -n "$GROQ" ]; then
    ok "Saved your keys to $CONFIG_FILE"
  else
    warn "No Groq key entered — add LLM_API_KEY + STT_API_KEY to $CONFIG_FILE before starting"
  fi
fi

# ── 5. Next steps ──────────────────────────────────────────────────
printf '\n'
ok "It's May is installed."
cat <<EOF

Start it — each command in its own terminal, from $INSTALL_DIR:

  source .venv/bin/activate

  1) scrappy serve     # the brain, running locally on your Mac (sovereign)
  2) scrappy worker    # lets it act on THIS machine (files, apps, Claude Code)
  3) scrappy seed      # load long-term memory — one time
  4) scrappy voice     # talk to it out loud   (or just:  scrappy "hello")

Sanity-check anytime:  scrappy status
EOF
