#!/bin/bash
# ACT-Claude Auto Cycle — run via cron or manually
# Uses Claude Code CLI (no API key needed)
#
# Cron example (every 30 min):
#   */30 * * * * /daten/docker/act/act-cron.sh >> /var/log/act-claude.log 2>&1

set -euo pipefail
echo "=== ACT Auto Cycle: $(date -Iseconds) ==="

# Verify claude CLI is available and authenticated
if ! /usr/local/bin/claude --version &>/dev/null; then
    echo "[ERROR] Claude Code CLI not found or not working"
    exit 1
fi

cd /daten/docker/act
python3 act-claude.py auto

echo "=== Done: $(date -Iseconds) ==="
