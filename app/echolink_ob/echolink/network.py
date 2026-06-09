from __future__ import annotations

import argparse
import json
import logging
import socket
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from echolink_ob.config import AppConfig, load_config
from echolink_ob.logging_setup import setup_logging

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UdpPortCheck:
    host: str
    port: int
    purpose: str
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class TcpReachabilityCheck:
    host: str
    port: int
    reachable: bool
    elapsed_ms: int
    reason: str = ""


@dataclass(frozen=True)
class EchoLinkPreflightReport:
    callsign: str
    max_connected_stations: int
    bind_host: str
    audio_port: int
    control_port: int
    directory_host: str
    directory_port: int
    udp_checks: list[UdpPortCheck]
    directory_check: TcpReachabilityCheck | None
    ok: bool
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "callsign": self.callsign,
            "max_connected_stations": self.max_connected_stations,
            "bind_host": self.bind_host,
            "audio_port": self.audio_port,
            "control_port": self.control_port,
            "directory_host": self.directory_host,
            "directory_port": self.directory_port,
            "udp_checks": [asdict(check) for check in self.udp_checks],
            "directory_check": asdict(self.directory_check) if self.directory_check else None,
            "ok": self.ok,
            "notes": self.notes,
        }


def _bind_udp_check(host: str, port: int, purpose: str) -> UdpPortCheck:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        return UdpPortCheck(host=host, port=port, purpose=purpose, available=True)
    except OSError as exc:
        return UdpPortCheck(
            host=host,
            port=port,
            purpose=purpose,
            available=False,
            reason=str(exc),
        )
    finally:
        sock.close()


def _tcp_reachability(host: str, port: int, timeout_s: float) -> TcpReachabilityCheck:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            elapsed = int((time.monotonic() - start) * 1000)
            return TcpReachabilityCheck(host=host, port=port, reachable=True, elapsed_ms=elapsed)
    except OSError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return TcpReachabilityCheck(
            host=host,
            port=port,
            reachable=False,
            elapsed_ms=elapsed,
            reason=str(exc),
        )


def run_echolink_preflight(
    cfg: AppConfig,
    *,
    skip_directory: bool = False,
    timeout_ms: int = 1500,
) -> EchoLinkPreflightReport:
    """Validate local EchoLink network readiness.

    This is not a login implementation.  It checks that the fixed EchoLink UDP
    ports can be bound locally and, optionally, that outbound TCP to the
    configured directory server is reachable.
    """

    bind_host = cfg.echolink.bind_host
    udp_checks = [
        _bind_udp_check(bind_host, cfg.echolink.audio_port, "EchoLink audio UDP"),
        _bind_udp_check(bind_host, cfg.echolink.control_port, "EchoLink control UDP"),
    ]

    directory_check = None
    if not skip_directory:
        directory_check = _tcp_reachability(
            cfg.echolink.directory_host,
            cfg.echolink.directory_port,
            timeout_s=max(0.1, timeout_ms / 1000),
        )

    notes = [
        "EchoLink conventionally uses UDP 5198 for audio, UDP 5199 for control, and TCP 5200 for directory/server access.",
        "This preflight does not log in to EchoLink; it only verifies local bindability and optional directory reachability.",
    ]
    callsign_ok = bool(cfg.echolink.callsign and cfg.echolink.callsign != "CHANGE_ME")
    password_ok = bool(cfg.echolink.password and cfg.echolink.password != "CHANGE_ME")
    if not callsign_ok:
        notes.append("EchoLink callsign is not configured.")
    if not password_ok:
        notes.append("EchoLink password is not configured.")

    ok = all(check.available for check in udp_checks) and callsign_ok and password_ok
    if directory_check is not None:
        ok = ok and directory_check.reachable

    return EchoLinkPreflightReport(
        callsign=cfg.echolink.callsign,
        max_connected_stations=cfg.echolink.max_connected_stations,
        bind_host=bind_host,
        audio_port=cfg.echolink.audio_port,
        control_port=cfg.echolink.control_port,
        directory_host=cfg.echolink.directory_host,
        directory_port=cfg.echolink.directory_port,
        udp_checks=udp_checks,
        directory_check=directory_check,
        ok=ok,
        notes=notes,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run EchoLink network preflight checks")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--output", default="/opt/echolink-ob/diagnostics/echolink-preflight.json")
    p.add_argument("--skip-directory", action="store_true", help="Skip outbound TCP directory-server reachability check")
    p.add_argument("--timeout-ms", type=int, default=1500)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level)
    report = run_echolink_preflight(
        cfg,
        skip_directory=args.skip_directory,
        timeout_ms=args.timeout_ms,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    print(f"report={output}")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
