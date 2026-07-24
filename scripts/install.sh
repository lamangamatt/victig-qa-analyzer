#!/usr/bin/env bash
# One-time installer for the QA Analyzer on the Mac mini.
#
# Reads the token + Anthropic key from ~/.qa-analyzer.env, installs the
# LaunchAgent, and starts the local server on 127.0.0.1:8890.
#
# Prereqs:
#   1. Anthropic key + generated token pasted into ~/.qa-analyzer.env
#   2. Python 3.13 in /opt/homebrew/bin/python3.13
#   3. Repo cloned to ~/clawd/victig-legal/qa-analyzer/
#
# Usage: bash scripts/install.sh

set -euo pipefail

REPO="$HOME/clawd/victig-legal/qa-analyzer"
LOGDIR="$HOME/Library/Logs"
LAUNCHDIR="$HOME/Library/LaunchAgents"
PLIST="ai.openclaw.qa-analyzer.plist"

echo "==> Verifying env file"
if [ ! -f "$HOME/.qa-analyzer.env" ]; then
    echo "❌ $HOME/.qa-analyzer.env not found."
    echo "   Copy scripts/qa-analyzer.env.template to ~/.qa-analyzer.env and fill in values."
    exit 1
fi

# shellcheck disable=SC1091
set -a
source "$HOME/.qa-analyzer.env"
set +a

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "❌ ANTHROPIC_API_KEY not set in ~/.qa-analyzer.env"
    exit 1
fi
if [ -z "${QA_ANALYZER_TOKEN:-}" ]; then
    echo "❌ QA_ANALYZER_TOKEN not set in ~/.qa-analyzer.env"
    echo "   Generate one with: openssl rand -hex 32"
    exit 1
fi

echo "==> Creating venv + installing deps"
cd "$REPO"
# Find a working Python 3.x from homebrew (avoids venvs pinning to a
# specific minor version that brew later upgrades away).
PY="$(command -v /opt/homebrew/bin/python3 || command -v python3)"
if [ ! -d ".venv" ] || ! "./.venv/bin/python" --version >/dev/null 2>&1; then
    echo "   (Re)creating venv with $PY"
    rm -rf .venv
    "$PY" -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

echo "==> Installing Streamlit LaunchAgent"
mkdir -p "$LAUNCHDIR" "$LOGDIR"
sed "s|__HOME__|$HOME|g" "$REPO/scripts/$PLIST" > "$LAUNCHDIR/$PLIST"

echo "==> Loading Streamlit LaunchAgent"
launchctl bootout "gui/$(id -u)/ai.openclaw.qa-analyzer" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$LAUNCHDIR/$PLIST"
launchctl kickstart -k "gui/$(id -u)/ai.openclaw.qa-analyzer"

echo "==> Waiting for local Streamlit server to come up (127.0.0.1:8890)..."
for i in {1..20}; do
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8890/qa-analyzer/_stcore/health" | grep -q "200\|303"; then
        echo "   ✅ Local Streamlit up on 127.0.0.1:8890"
        break
    fi
    sleep 1
done

echo "==> Installing Caddy prefix-proxy LaunchAgent"
# Ensures Caddy is installed
if ! command -v caddy >/dev/null 2>&1; then
    echo "   Installing Caddy via brew..."
    brew install caddy
fi
CADDY_PLIST="ai.openclaw.qa-analyzer-caddy.plist"
sed "s|__HOME__|$HOME|g" "$REPO/scripts/$CADDY_PLIST" > "$LAUNCHDIR/$CADDY_PLIST"
launchctl bootout "gui/$(id -u)/ai.openclaw.qa-analyzer-caddy" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$LAUNCHDIR/$CADDY_PLIST"
launchctl kickstart -k "gui/$(id -u)/ai.openclaw.qa-analyzer-caddy"

echo "==> Waiting for Caddy prefix-proxy on 127.0.0.1:8891..."
for i in {1..10}; do
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8891/_stcore/health" | grep -q "200"; then
        echo "   ✅ Caddy up on 127.0.0.1:8891"
        break
    fi
    sleep 1
done

echo "==> Registering Tailscale Funnel route → Caddy"
/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg --set-path=/qa-analyzer --https=443 http://127.0.0.1:8891

echo ""
echo "✅ Install complete."
echo ""
echo "Public URL (token gate active):"
echo "   https://matts-mac-mini.tailefa08d.ts.net/qa-analyzer/?token=$QA_ANALYZER_TOKEN"
echo ""
echo "Logs:"
echo "   tail -f $LOGDIR/qa-analyzer.log"
echo "   tail -f $LOGDIR/qa-analyzer.err.log"
echo ""
echo "Manage the service:"
echo "   launchctl kickstart -k gui/\$(id -u)/ai.openclaw.qa-analyzer   # restart"
echo "   launchctl bootout gui/\$(id -u)/ai.openclaw.qa-analyzer         # stop"
