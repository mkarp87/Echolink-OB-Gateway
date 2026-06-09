from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from echolink_ob.analog.ports import (
    PortPlanError,
    build_port_plan,
    render_analog_bridge_ini,
    write_analog_bridge_ini,
    write_state_file,
)
from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging
from echolink_ob.openbridge.test_sender import make_client

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="echolink-ob runtime/preflight command")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument(
        "--no-write-analog-config",
        action="store_true",
        help="validate the Analog_Bridge port plan but do not write generated files",
    )
    p.add_argument(
        "--monitor-openbridge",
        action="store_true",
        help="after preflight, monitor OpenBridge packets until Ctrl+C",
    )
    p.add_argument("--seconds", type=float, default=None, help="monitor duration when --monitor-openbridge is used")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)

    log.info("echolink_ob_run_start config=%s", args.config)
    try:
        result = build_port_plan(cfg, allow_in_use=False, reuse_state=True)
    except PortPlanError as exc:
        log.error("analog_port_plan_failed error=%s", exc)
        print(f"ERROR: {exc}")
        return 2

    ini_text = render_analog_bridge_ini(cfg, result.plan)
    if not args.no_write_analog_config:
        write_state_file(cfg.port_manager.state_file, result)
        write_analog_bridge_ini(result.plan.analog_bridge_ini_path, ini_text)
        log.info(
            "analog_port_plan_written state=%s ini=%s ports=%s reused=%s",
            cfg.port_manager.state_file,
            result.plan.analog_bridge_ini_path,
            result.plan.ports,
            result.reused_state,
        )

    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    print(f"analog_bridge_ini={result.plan.analog_bridge_ini_path}")
    print("status=preflight_ok")
    print("note=preflight validates config and generated Analog_Bridge ports")

    if args.monitor_openbridge:
        client = make_client(cfg)
        try:
            log.info("openbridge_monitor_start seconds=%s", args.seconds)
            for _pkt in client.listen(args.seconds):
                pass
        except KeyboardInterrupt:
            log.info("openbridge_monitor_interrupted")
            return 130
        finally:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
