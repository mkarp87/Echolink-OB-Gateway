from __future__ import annotations

import argparse
import json
import logging
import threading
from pathlib import Path

from echolink_ob.analog.ports import build_port_plan, render_analog_bridge_ini, write_analog_bridge_ini, write_state_file
from echolink_ob.analog.tlv import RawTlvFrame, listen_raw_tlv, write_tlv_capture
from echolink_ob.analog.usrp import pcm_sine_frames, send_usrp_pcm_frames
from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Send an audible USRP test tone into Analog_Bridge and capture raw TLV output")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--frequency", type=float, default=1000.0)
    p.add_argument("--amplitude", type=int, default=6000)
    p.add_argument("--capture-seconds", type=float, default=6.0)
    p.add_argument("--output-dir", default="/opt/echolink-ob/diagnostics/analog-tone")
    p.add_argument("--no-write-analog-config", action="store_true")
    p.add_argument("--no-capture", action="store_true", help="send tone only; do not bind/capture AMBE_AUDIO TLV output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)

    # During this test, Analog_Bridge may already be listening on app TX ports, so only app RX ports are bound here.
    result = build_port_plan(cfg, allow_in_use=True, reuse_state=True)
    if not args.no_write_analog_config:
        write_state_file(cfg.port_manager.state_file, result)
        write_analog_bridge_ini(result.plan.analog_bridge_ini_path, render_analog_bridge_ini(cfg, result.plan))

    plan = result.plan
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    captured: list[RawTlvFrame] = []

    def capture_tlv() -> None:
        try:
            for frame in listen_raw_tlv(plan.host, plan.app_tlv_rx_port, seconds=args.capture_seconds):
                captured.append(frame)
                log.info("analog_tlv_received bytes=%s source=%s:%s", len(frame.data), frame.source[0], frame.source[1])
        except OSError as exc:
            log.error("analog_tlv_capture_failed port=%s error=%s", plan.app_tlv_rx_port, exc)

    thread: threading.Thread | None = None
    if not args.no_capture:
        thread = threading.Thread(target=capture_tlv, daemon=True)
        thread.start()

    frames = pcm_sine_frames(seconds=args.seconds, frequency_hz=args.frequency, amplitude=args.amplitude)
    sent = send_usrp_pcm_frames(
        target_host=plan.host,
        target_port=plan.app_usrp_tx_port,
        frames=frames,
        talkgroup=cfg.openbridge.fixed_tgid,
    )
    log.info(
        "analog_usrp_tone_sent frames=%s host=%s port=%s seconds=%s frequency=%s",
        sent,
        plan.host,
        plan.app_usrp_tx_port,
        args.seconds,
        args.frequency,
    )

    if thread is not None:
        thread.join(timeout=args.capture_seconds + 1.0)

    capture_path = output_dir / "analog-tone.tlvraw"
    if captured:
        write_tlv_capture(capture_path, captured)

    report = {
        "usrp_tone_frames_sent": sent,
        "usrp_target": {"host": plan.host, "port": plan.app_usrp_tx_port},
        "tlv_capture": {"host": plan.host, "port": plan.app_tlv_rx_port, "frames": len(captured), "file": str(capture_path) if captured else None},
        "analog_bridge_ini": result.plan.analog_bridge_ini_path,
        "note": "This tests app -> USRP -> Analog_Bridge -> AMBE_AUDIO TLV output. It does not yet forward TLV into OpenBridge.",
    }
    report_path = output_dir / "analog-tone-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
