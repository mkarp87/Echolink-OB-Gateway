#!/usr/bin/env bash
set -euo pipefail

DURATION="${1:-30}"
SERVICE_NAME="${2:-echolink-ob}"
BASE_DIR="${ECHOLINK_OB_DIR:-/opt/echolink-ob}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${BASE_DIR}/diagnostics/audio-debug-${TS}"
mkdir -p "${OUT_DIR}"

note() { printf '%s\n' "$*"; }
run_capture() {
  local cmd_name="$1"; shift
  {
    printf '$ %s\n' "$*"
    "$@"
  } > "${OUT_DIR}/${cmd_name}.txt" 2>&1 || true
}

note "Writing diagnostics to ${OUT_DIR}"
note "Do not attach config/config.toml; this script intentionally avoids it."

run_capture uname uname -a
run_capture python python3 --version
run_capture date date -u
run_capture ss_udp ss -lunp
run_capture systemd_status systemctl status "${SERVICE_NAME}" --no-pager
run_capture hblink3_status systemctl status hblink3 --no-pager
run_capture analog_bridge_status systemctl status Analog_Bridge --no-pager
run_capture analog_bridge_status_lower systemctl status analog_bridge --no-pager
run_capture journal_gateway journalctl -u "${SERVICE_NAME}" -n 600 --no-pager
run_capture journal_hblink3 journalctl -u hblink3 -n 600 --no-pager
run_capture journal_analog_bridge journalctl -u Analog_Bridge -n 600 --no-pager
run_capture journal_analog_bridge_lower journalctl -u analog_bridge -n 600 --no-pager

for f in \
  "${BASE_DIR}/logs/full-status.json" \
  "${BASE_DIR}/logs/bridge-status.json" \
  "${BASE_DIR}/logs/echolink-status.json" \
  "${BASE_DIR}/data/port-plan.json" \
  "${BASE_DIR}/generated/Analog_Bridge.ini"; do
  if [ -f "${f}" ]; then
    cp -a "${f}" "${OUT_DIR}/$(basename "${f}")"
  fi
done

if command -v tcpdump >/dev/null 2>&1; then
  note "Starting ${DURATION}s UDP capture. Key EchoLink -> DMR and then DMR -> EchoLink during this window."
  FILTER='udp and (port 5198 or port 5199 or port 54095 or port 54096 or portrange 23000-23199 or port 2460 or port 2990)'
  timeout "${DURATION}" tcpdump -i any -nn -tt -s 0 -w "${OUT_DIR}/audio_path.pcap" "${FILTER}" > "${OUT_DIR}/tcpdump_capture.txt" 2>&1 || true
  tcpdump -nn -tt -r "${OUT_DIR}/audio_path.pcap" > "${OUT_DIR}/audio_path.txt" 2>&1 || true
  if [ -f "${BASE_DIR}/scripts/summarize_tcpdump_udp.py" ]; then
    python3 "${BASE_DIR}/scripts/summarize_tcpdump_udp.py" < "${OUT_DIR}/audio_path.txt" > "${OUT_DIR}/audio_path_summary.json" 2> "${OUT_DIR}/audio_path_summary.err" || true
  elif [ -f "$(dirname "$0")/summarize_tcpdump_udp.py" ]; then
    python3 "$(dirname "$0")/summarize_tcpdump_udp.py" < "${OUT_DIR}/audio_path.txt" > "${OUT_DIR}/audio_path_summary.json" 2> "${OUT_DIR}/audio_path_summary.err" || true
  fi
else
  note "tcpdump not found; install it for packet-level diagnostics: apt install tcpdump" | tee "${OUT_DIR}/tcpdump_missing.txt"
fi

ARCHIVE="${OUT_DIR}.tar.gz"
tar -czf "${ARCHIVE}" -C "$(dirname "${OUT_DIR}")" "$(basename "${OUT_DIR}")"
note "Created ${ARCHIVE}"
