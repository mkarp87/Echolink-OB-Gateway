from __future__ import annotations

import argparse
import json
import logging
import socket
import time
from pathlib import Path

from echolink_ob.analog.ports import build_port_plan, render_analog_bridge_ini, write_analog_bridge_ini, write_state_file
from echolink_ob.analog.usrp import USRP_VOICE_FRAME_BYTES, UsrpPacket
from echolink_ob.audio.wavdiag import write_wav
from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging

log = logging.getLogger(__name__)


def capture_usrp_pcm(
    *,
    host: str,
    port: int,
    seconds: float,
    output_wav: str | Path,
    idle_timeout_s: float = 2.0,
) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(0.25)
    start = time.monotonic()
    last_voice = None
    packets = 0
    voice_packets = 0
    unkey_packets = 0
    bad_packets = 0
    pcm = bytearray()
    try:
        while time.monotonic() - start < seconds:
            if last_voice is not None and time.monotonic() - last_voice >= idle_timeout_s:
                log.info("usrp_capture_idle_timeout seconds=%s", idle_timeout_s)
                break
            try:
                data, src = sock.recvfrom(4096)
            except socket.timeout:
                continue
            packets += 1
            try:
                pkt = UsrpPacket.from_bytes(data)
            except Exception as exc:
                bad_packets += 1
                log.warning("usrp_capture_bad_packet bytes=%s error=%s", len(data), exc)
                continue
            if pkt.keyup and pkt.payload:
                if len(pkt.payload) == USRP_VOICE_FRAME_BYTES:
                    pcm.extend(pkt.payload)
                    voice_packets += 1
                    last_voice = time.monotonic()
                    log.info("usrp_capture_voice seq=%s bytes=%s source=%s:%s", pkt.sequence, len(pkt.payload), src[0], src[1])
                else:
                    bad_packets += 1
                    log.warning("usrp_capture_bad_voice_len seq=%s bytes=%s", pkt.sequence, len(pkt.payload))
            else:
                unkey_packets += 1
                log.info("usrp_capture_unkey seq=%s source=%s:%s", pkt.sequence, src[0], src[1])
                if pcm:
                    break
    finally:
        sock.close()
    output_wav = Path(output_wav)
    if pcm:
        write_wav(output_wav, bytes(pcm), sample_rate=8000)
    return {
        "host": host,
        "port": port,
        "output_wav": str(output_wav) if pcm else None,
        "packets": packets,
        "voice_packets": voice_packets,
        "unkey_packets": unkey_packets,
        "bad_packets": bad_packets,
        "pcm_bytes": len(pcm),
        "audio_ms": int((len(pcm) / 2) / 8000 * 1000) if pcm else 0,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Capture decoded USRP PCM from Analog_Bridge and write a diagnostic WAV")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--idle-timeout", type=float, default=2.0)
    p.add_argument("--output", default="/opt/echolink-ob/diagnostics/usrp-capture/usrp-capture.wav")
    p.add_argument("--no-write-analog-config", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    result = build_port_plan(cfg, allow_in_use=True, reuse_state=True)
    if not args.no_write_analog_config:
        write_state_file(cfg.port_manager.state_file, result)
        write_analog_bridge_ini(result.plan.analog_bridge_ini_path, render_analog_bridge_ini(cfg, result.plan))
    out = capture_usrp_pcm(
        host=result.plan.host,
        port=result.plan.app_usrp_rx_port,
        seconds=args.seconds,
        output_wav=args.output,
        idle_timeout_s=args.idle_timeout,
    )
    report_path = Path(args.output).with_suffix(".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))
    print(f"report={report_path}")
    return 0 if out["voice_packets"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
