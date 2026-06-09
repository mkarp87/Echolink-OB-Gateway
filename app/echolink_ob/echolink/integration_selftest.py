from __future__ import annotations

import argparse
import json
import logging
import socket
import tempfile
import threading
import time
from pathlib import Path

from echolink_ob.analog.ports import build_port_plan, render_analog_bridge_ini, write_analog_bridge_ini, write_state_file
from echolink_ob.analog.usrp import USRP_VOICE_FRAME_BYTES, UsrpPacket, pcm_sine_frames
from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging

from .gsm import GSM_PCM_BYTES, Gsm610Codec
from .rtp import build_gsm_rtp, build_rtcp_bye, build_rtcp_sdes, parse_rtp
from .service import EchoLinkUdpConferenceService

log = logging.getLogger(__name__)


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def run_selftest(cfg_path: str, output_dir: str) -> dict[str, object]:
    cfg = load_config(cfg_path)
    result = build_port_plan(cfg, allow_in_use=True, reuse_state=True)
    write_state_file(cfg.port_manager.state_file, result)
    write_analog_bridge_ini(result.plan.analog_bridge_ini_path, render_analog_bridge_ini(cfg, result.plan))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    service = EchoLinkUdpConferenceService(cfg, status_file=out / "echolink-status.json")
    thread = threading.Thread(target=lambda: service.run(seconds=4.0), daemon=True)
    thread.start()
    time.sleep(0.25)

    ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ctrl.bind(("127.0.0.1", _free_udp_port()))
    audio.bind(("127.0.0.1", _free_udp_port()))
    ctrl.settimeout(1.0)
    audio.settimeout(1.0)
    try:
        ctrl.sendto(build_rtcp_sdes(callsign="K1ABC-L", name="Test One"), ("127.0.0.1", cfg.echolink.control_port))
        try:
            ctrl.recvfrom(2048)
            sdes_response = True
        except socket.timeout:
            sdes_response = False
        with Gsm610Codec.create() as codec:
            pcm = b"".join(pcm_sine_frames(seconds=0.08, frequency_hz=600, amplitude=5000))
            gsm_payload = codec.encode_pcm(pcm)
        audio.sendto(build_gsm_rtp(gsm_payload, sequence=1), ("127.0.0.1", cfg.echolink.audio_port))

        # Fake decoded DMR PCM from Analog_Bridge into the EchoLink service USRP RX port.
        usrp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        pkt = UsrpPacket(sequence=99, keyup=True, payload=bytes(next(pcm_sine_frames(seconds=0.02, frequency_hz=900))))
        usrp.sendto(pkt.to_bytes(), ("127.0.0.1", result.plan.app_usrp_rx_port))
        time.sleep(0.5)
        ctrl.sendto(build_rtcp_bye(), ("127.0.0.1", cfg.echolink.control_port))
    finally:
        ctrl.close()
        audio.close()
    thread.join(timeout=5.0)
    report = {
        "sdes_response": sdes_response,
        "stats": service.stats.__dict__.copy(),
        "status_file": str(out / "echolink-status.json"),
        "ok": sdes_response and service.stats.stations_connected >= 1 and service.stats.gsm_packets_decoded >= 1 and service.stats.pcm_frames_to_usrp >= 1,
    }
    (out / "echolink-integration-selftest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run an internal EchoLink UDP service integration self-test")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--output-dir", default="/opt/echolink-ob/diagnostics/echolink-integration")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    report = run_selftest(args.config, args.output_dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"report={Path(args.output_dir) / 'echolink-integration-selftest.json'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
