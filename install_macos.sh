#!/bin/bash
# Install Cathay Award Monitor as a macOS background service (launchd)
# Usage: bash install_macos.sh [--uninstall]

set -e

PLIST_LABEL="com.cathay.awardmonitor"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/cathay-monitor.log"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "$1" == "--uninstall" ]]; then
    echo "Stopping and removing Cathay Award Monitor..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "Done. Log file kept at: $LOG_FILE"
    exit 0
fi

# ── Detect Python ─────────────────────────────────────────────────────────────
PYTHON=$(command -v python3)
if [[ -z "$PYTHON" ]]; then
    echo "Error: python3 not found. Install from https://python.org or via Homebrew."
    exit 1
fi
echo "Using Python: $PYTHON ($($PYTHON --version))"

# ── Check .env exists ─────────────────────────────────────────────────────────
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo ""
    echo "No .env found — copying .env.example to .env"
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "Edit $PROJECT_DIR/.env to add your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
fi

# ── Write plist ───────────────────────────────────────────────────────────────
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${PROJECT_DIR}/main.py</string>
        <string>run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <!-- Start on login and restart if it crashes -->
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <!-- Wait 30s before restarting after a crash -->
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <!-- Logs -->
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>

    <!-- Environment: inherit PATH so playwright/chromium is found -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
</dict>
</plist>
EOF

echo "Plist written to: $PLIST_PATH"

# ── Load service ──────────────────────────────────────────────────────────────
# Unload first in case it was already loaded (e.g. re-install)
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo ""
echo "✅ Cathay Award Monitor installed and started!"
echo ""
echo "Commands:"
echo "  Check status : launchctl list | grep cathay"
echo "  View logs    : tail -f $LOG_FILE"
echo "  Stop         : launchctl unload $PLIST_PATH"
echo "  Start        : launchctl load   $PLIST_PATH"
echo "  Uninstall    : bash $PROJECT_DIR/install_macos.sh --uninstall"
echo ""
echo "The monitor will:"
echo "  • Start automatically on every login"
echo "  • Restart automatically if it crashes"
echo "  • Poll every 30 minutes (configurable in .env)"
echo "  • Push Telegram notifications when Business class awards are found"
