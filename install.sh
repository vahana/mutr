#!/usr/bin/env bash
set -euo pipefail

# mutr installer — no sudo, everything in ~/.local
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/.../master/install.sh | bash
#   ./install.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

say() { printf "${CYAN}==>${NC} %s\n" "$1"; }
ok()  { printf "${GREEN}  ok${NC} %s\n" "$1"; }
die() { printf "${RED}  error${NC} %s\n" "$1"; exit 1; }

LOCAL="$HOME/.local"
BREW="$LOCAL/brew"
UV_BIN="$HOME/.local/bin"

say "mutr installer -- ~/.local, no sudo"

# ── uv ──────────────────────────────────────────────────────────────────────
if command -v uv &>/dev/null; then
    ok "uv $(uv --version 2>&1 | head -1)"
else
    say "installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv install failed"
    ok "uv installed"
fi
export PATH="$UV_BIN:$PATH"

# ── user-local homebrew ─────────────────────────────────────────────────────
if [ -x "$BREW/bin/brew" ]; then
    ok "brew found at $BREW/bin/brew"
else
    say "installing homebrew to $BREW..."
    mkdir -p "$BREW"
    curl -LsSf https://github.com/Homebrew/brew/tarball/master | \
        tar xz --strip-components 1 -C "$BREW" || die "brew install failed"
    ok "brew installed"
fi

export PATH="$BREW/bin:$BREW/sbin:$PATH"
export HOMEBREW_PREFIX="$BREW"
export HOMEBREW_CELLAR="$BREW/Cellar"
export HOMEBREW_REPOSITORY="$BREW"
export HOMEBREW_NO_AUTO_UPDATE=1

_brew() { "$BREW/bin/brew" "$@"; }

# ── ffmpeg ──────────────────────────────────────────────────────────────────
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1)"
elif _brew list ffmpeg &>/dev/null 2>&1; then
    ok "ffmpeg (brew, not on PATH)"
else
    say "installing ffmpeg..."
    _brew install ffmpeg || die "ffmpeg install failed"
    ok "ffmpeg installed"
fi

# ── rubberband ──────────────────────────────────────────────────────────────
if command -v rubberband &>/dev/null; then
    ok "rubberband found"
elif _brew list rubberband &>/dev/null 2>&1; then
    ok "rubberband (brew, not on PATH)"
else
    say "installing rubberband..."
    _brew install rubberband || die "rubberband install failed"
    ok "rubberband installed"
fi

# ── shell profile ───────────────────────────────────────────────────────────
PROFILE=""
for f in "$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    [ -f "$f" ] && { PROFILE="$f"; break; }
done
[ -z "$PROFILE" ] && PROFILE="$HOME/.zprofile"

NEED_ADD=false
grep -q "$UV_BIN" "$PROFILE" 2>/dev/null || NEED_ADD=true
grep -q "$BREW/bin" "$PROFILE" 2>/dev/null || NEED_ADD=true

if $NEED_ADD; then
    say "adding paths to $PROFILE..."
    cat >> "$PROFILE" <<'PROFILE_EOF'

# mutr
export PATH="$HOME/.local/bin:$PATH"
export HOMEBREW_PREFIX="$HOME/.local/brew"
export HOMEBREW_CELLAR="$HOME/.local/brew/Cellar"
export HOMEBREW_REPOSITORY="$HOME/.local/brew"
export PATH="$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin:$PATH"
PROFILE_EOF
    ok "paths added -- restart shell or: exec $SHELL -l"
else
    ok "paths already in $PROFILE"
fi

# ── done ────────────────────────────────────────────────────────────────────
echo ""
say "ready."
echo ""
echo "  cd /path/to/mutr && ./mutr.py"
echo ""

# verify
command -v uv        >/dev/null 2>&1 || die "uv not on PATH -- restart your shell"
command -v ffmpeg    >/dev/null 2>&1 || die "ffmpeg not on PATH -- restart your shell"
command -v rubberband >/dev/null 2>&1 || die "rubberband not on PATH -- restart your shell"
ok "all tools on PATH"
