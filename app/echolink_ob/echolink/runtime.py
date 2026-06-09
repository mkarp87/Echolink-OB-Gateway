from __future__ import annotations

import argparse
import json
import logging
import signal
from pathlib import Path

from echolink_ob.analog.ports import build_port_plan, render_analog_bridge_ini, write_analog_bridge_ini, write_state_file
from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging

from .service import EchoLinkUdpConferenceService, require_gsm

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the EchoLink UDP conference side")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--seconds", type=float, default=0.0, help="Run duration; 0 means until interrupted")
    p.add_argument("--status-file", default="/opt/echolink-ob/logs/echolink-status.json")
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    require_gsm()
    plan_result = build_port_plan(cfg, allow_in_use=True, reuse_state=True)
    write_state_file(cfg.port_manager.state_file, plan_result)
    write_analog_bridge_ini(plan_result.plan.analog_bridge_ini_path, render_analog_bridge_ini(cfg, plan_result.plan))
    report = {
        "status": "echolink_runtime_ready",
        "callsign": cfg.echolink.callsign,
        "audio_bind": [cfg.echolink.bind_host, cfg.echolink.audio_port],
        "control_bind": [cfg.echolink.bind_host, cfg.echolink.control_port],
        "max_connected_stations": cfg.echolink.max_connected_stations,
        "usrp_rx": [plan_result.plan.host, plan_result.plan.app_usrp_rx_port],
        "usrp_tx": [plan_result.plan.host, plan_result.plan.app_usrp_tx_port],
        "status_file": args.status_file,
        "note": "EchoLink UDP conference side. Directory registration is handled by the full runtime.",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.dry_run:
        return 0
    service = EchoLinkUdpConferenceService(cfg, status_file=args.status_file)
    stop = {"value": False}

    def _stop(_signum, _frame):
        stop["value"] = True
        service.request_stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        service.run(seconds=args.seconds if args.seconds > 0 else None)
    except KeyboardInterrupt:
        service.request_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
