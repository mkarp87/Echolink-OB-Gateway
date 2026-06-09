#!/usr/bin/env bash
set -euo pipefail

DURATION="${1:-30}"
SERVICE="${2:-echolink-ob}"
APP_ROOT="${APP_ROOT:-/opt/echolink-ob}"
CFG="$APP_ROOT/config/config.toml"
OUTDIR="$APP_ROOT/diagnostics/echolink-disconnect-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUTDIR"

if [[ ! -f "$CFG" ]]; then
  echo "Config not found: $CFG" >&2
  exit 1
fi

read -r BIND_HOST AUDIO_PORT CONTROL_PORT <<<"$(python3 - <<PY
import tomllib
from pathlib import Path
cfg = tomllib.loads(Path('$CFG').read_text())
e = cfg.get('echolink', {})
print(e.get('bind_host', '0.0.0.0'), int(e.get('audio_port', 5198)), int(e.get('control_port', 5199)))
PY
)"

FILTER="udp and (port $AUDIO_PORT or port $CONTROL_PORT)"

echo "Capturing EchoLink disconnect traffic for ${DURATION}s"
echo "App root:      $APP_ROOT"
echo "Service:       $SERVICE"
echo "Bind host:     $BIND_HOST"
echo "Audio port:    $AUDIO_PORT"
echo "Control port:  $CONTROL_PORT"
echo "Output dir:    $OUTDIR"
echo

echo "Instructions:"
echo "  1. Start with the EchoLink phone client connected."
echo "  2. Wait 3 seconds."
echo "  3. Disconnect from the phone client."
echo "  4. Wait for this script to finish."
echo

cp -a "$CFG" "$OUTDIR/config.toml.redacted" || true
sed -i -E 's/(password[[:space:]]*=[[:space:]]*)"[^"]*"/\1"REDACTED"/g; s/(passphrase[[:space:]]*=[[:space:]]*)"[^"]*"/\1"REDACTED"/g' "$OUTDIR/config.toml.redacted" || true

cp -a "$APP_ROOT/logs/echolink-status.json" "$OUTDIR/echolink-status-before.json" 2>/dev/null || true
cp -a "$APP_ROOT/logs/full-status.json" "$OUTDIR/full-status-before.json" 2>/dev/null || true
systemctl status "$SERVICE" --no-pager > "$OUTDIR/systemctl-status-before.txt" 2>&1 || true
ss -lunp > "$OUTDIR/ss-lunp-before.txt" 2>&1 || true
journalctl -u "$SERVICE" -n 300 --no-pager > "$OUTDIR/journal-before.txt" 2>&1 || true

timeout "$DURATION" tcpdump -i any -s 0 -nn -vv -w "$OUTDIR/echolink-disconnect.pcap" "$FILTER" > "$OUTDIR/tcpdump.out" 2> "$OUTDIR/tcpdump.err" || true

sleep 1
cp -a "$APP_ROOT/logs/echolink-status.json" "$OUTDIR/echolink-status-after.json" 2>/dev/null || true
cp -a "$APP_ROOT/logs/full-status.json" "$OUTDIR/full-status-after.json" 2>/dev/null || true
systemctl status "$SERVICE" --no-pager > "$OUTDIR/systemctl-status-after.txt" 2>&1 || true
ss -lunp > "$OUTDIR/ss-lunp-after.txt" 2>&1 || true
journalctl -u "$SERVICE" -n 500 --no-pager > "$OUTDIR/journal-after.txt" 2>&1 || true

cat > "$OUTDIR/README.txt" <<README
EchoLink disconnect capture.

Upload the tar.gz file from this directory back to ChatGPT for inspection.
The PCAP may include IP addresses and EchoLink identifiers. It should not include long voice audio if you only disconnected during the capture.
README

TAR="$OUTDIR.tar.gz"
tar -C "$(dirname "$OUTDIR")" -czf "$TAR" "$(basename "$OUTDIR")"
echo
echo "Created: $TAR"
