from __future__ import annotations

import argparse
import json
import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from echolink_ob.config import AppConfig, load_config


class PortPlanError(RuntimeError):
    """Raised when a safe Analog_Bridge port plan cannot be created."""


@dataclass(frozen=True)
class PortUseCheck:
    port: int
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class AnalogPortPlan:
    host: str
    range_start: int
    range_end: int
    app_usrp_rx_port: int
    app_usrp_tx_port: int
    app_tlv_rx_port: int
    app_tlv_tx_port: int
    analog_bridge_ini_path: str

    @property
    def ports(self) -> tuple[int, int, int, int]:
        return (
            self.app_usrp_rx_port,
            self.app_usrp_tx_port,
            self.app_tlv_rx_port,
            self.app_tlv_tx_port,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanResult:
    plan: AnalogPortPlan
    checks: tuple[PortUseCheck, ...]
    reused_state: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.as_dict(),
            "checks": [asdict(c) for c in self.checks],
            "reused_state": self.reused_state,
            "analog_bridge_mapping": analog_bridge_mapping(self.plan),
        }


def parse_port_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.lower() == "auto":
            return None
        return int(value)
    return int(value)


def validate_port_number(port: int) -> None:
    if port < 1 or port > 65535:
        raise PortPlanError(f"invalid UDP port: {port}")


def udp_port_available(host: str, port: int) -> PortUseCheck:
    validate_port_number(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        return PortUseCheck(port=port, available=True)
    except OSError as exc:
        return PortUseCheck(port=port, available=False, reason=str(exc))
    finally:
        sock.close()


def validate_range(start: int, end: int) -> None:
    validate_port_number(start)
    validate_port_number(end)
    if start > end:
        raise PortPlanError(f"port range start {start} is greater than end {end}")
    if end - start + 1 < 4:
        raise PortPlanError("port range must contain at least four ports")


def load_state_file(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_state_file(path: str | Path, result: PlanResult) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(result.as_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


def plan_from_state(state: dict[str, Any]) -> AnalogPortPlan:
    plan = state.get("plan", state)
    return AnalogPortPlan(
        host=str(plan["host"]),
        range_start=int(plan["range_start"]),
        range_end=int(plan["range_end"]),
        app_usrp_rx_port=int(plan["app_usrp_rx_port"]),
        app_usrp_tx_port=int(plan["app_usrp_tx_port"]),
        app_tlv_rx_port=int(plan["app_tlv_rx_port"]),
        app_tlv_tx_port=int(plan["app_tlv_tx_port"]),
        analog_bridge_ini_path=str(plan["analog_bridge_ini_path"]),
    )


def ports_unique(ports: tuple[int, ...]) -> bool:
    return len(set(ports)) == len(ports)


def analog_bridge_mapping(plan: AnalogPortPlan) -> dict[str, dict[str, int | str]]:
    return {
        "USRP": {
            "address": plan.host,
            "txPort": plan.app_usrp_rx_port,
            "rxPort": plan.app_usrp_tx_port,
            "note": "Analog_Bridge txPort sends decoded PCM to the app; rxPort receives PCM from the app.",
        },
        "AMBE_AUDIO": {
            "address": plan.host,
            "txPort": plan.app_tlv_rx_port,
            "rxPort": plan.app_tlv_tx_port,
            "note": "Analog_Bridge txPort sends encoded DMR/TLV to the app; rxPort receives encoded DMR/TLV from the app.",
        },
    }


def _configured_ports(cfg: AppConfig) -> tuple[int | None, int | None, int | None, int | None]:
    ab = cfg.analog_bridge
    return (
        parse_port_value(ab.app_usrp_rx_port),
        parse_port_value(ab.app_usrp_tx_port),
        parse_port_value(ab.app_tlv_rx_port),
        parse_port_value(ab.app_tlv_tx_port),
    )


def _reserved_ports(cfg: AppConfig) -> set[int]:
    reserved: set[int] = set(int(p) for p in cfg.port_manager.reserved_ports)
    if cfg.openbridge.local_bind_port:
        reserved.add(int(cfg.openbridge.local_bind_port))
    reserved.add(int(cfg.ambeserver.port))
    reserved.add(int(cfg.md380emu.port))
    return reserved


def _check_plan_available(plan: AnalogPortPlan, *, allow_in_use: bool) -> tuple[PortUseCheck, ...]:
    checks = tuple(udp_port_available(plan.host, port) for port in plan.ports)
    unavailable = [c for c in checks if not c.available]
    if unavailable and not allow_in_use:
        details = ", ".join(f"{c.port} ({c.reason})" for c in unavailable)
        raise PortPlanError(f"required Analog_Bridge UDP port(s) already in use: {details}")
    return checks


def _validate_plan(plan: AnalogPortPlan, cfg: AppConfig) -> None:
    validate_range(plan.range_start, plan.range_end)
    if not ports_unique(plan.ports):
        raise PortPlanError(f"Analog_Bridge ports must be unique, got {plan.ports}")
    for port in plan.ports:
        validate_port_number(port)
        if port < plan.range_start or port > plan.range_end:
            raise PortPlanError(f"port {port} is outside configured range {plan.range_start}-{plan.range_end}")
    conflicts = sorted(set(plan.ports) & _reserved_ports(cfg))
    if conflicts:
        raise PortPlanError(f"Analog_Bridge port(s) conflict with reserved ports: {conflicts}")


def build_port_plan(cfg: AppConfig, *, allow_in_use: bool = False, reuse_state: bool | None = None) -> PlanResult:
    pm = cfg.port_manager
    ab = cfg.analog_bridge
    reuse = pm.reuse_existing_allocation if reuse_state is None else reuse_state
    validate_range(pm.range_start, pm.range_end)
    explicit = _configured_ports(cfg)
    need_auto = [p is None for p in explicit]

    if reuse and any(need_auto):
        state = load_state_file(pm.state_file)
        if state:
            existing = plan_from_state(state)
            if existing.host == pm.host and existing.range_start == pm.range_start and existing.range_end == pm.range_end and existing.analog_bridge_ini_path == ab.ini_path:
                _validate_plan(existing, cfg)
                checks = _check_plan_available(existing, allow_in_use=allow_in_use)
                return PlanResult(plan=existing, checks=checks, reused_state=True)

    reserved = _reserved_ports(cfg)
    allocated: list[int] = []

    def choose_auto() -> int:
        for port in range(pm.range_start, pm.range_end + 1):
            if port in reserved or port in allocated:
                continue
            check = udp_port_available(pm.host, port)
            if check.available or allow_in_use:
                allocated.append(port)
                return port
        raise PortPlanError(f"no available UDP port found in configured range {pm.range_start}-{pm.range_end}")

    planned_values: list[int] = []
    for maybe_port in explicit:
        if maybe_port is None:
            planned_values.append(choose_auto())
        else:
            validate_port_number(maybe_port)
            planned_values.append(maybe_port)
            allocated.append(maybe_port)

    plan = AnalogPortPlan(
        host=pm.host,
        range_start=pm.range_start,
        range_end=pm.range_end,
        app_usrp_rx_port=planned_values[0],
        app_usrp_tx_port=planned_values[1],
        app_tlv_rx_port=planned_values[2],
        app_tlv_tx_port=planned_values[3],
        analog_bridge_ini_path=ab.ini_path,
    )
    _validate_plan(plan, cfg)
    checks = _check_plan_available(plan, allow_in_use=allow_in_use)
    return PlanResult(plan=plan, checks=checks, reused_state=False)


def render_analog_bridge_ini(cfg: AppConfig, plan: AnalogPortPlan) -> str:
    ab = cfg.analog_bridge
    ob = cfg.openbridge
    lines = [
        "; Generated by echolink-ob. Do not hand-edit these port pairings.",
        "; Re-run: echolink-ob-analog-plan --config /opt/echolink-ob/config/config.toml --write",
        "",
        "; include intentionally omitted unless macros are configured",
        "",
        "[GENERAL]",
        f"logLevel = {ab.log_level}",
        f"exportMetadata = {'true' if ab.export_metadata else 'false'}",
        f"transferRootDir = {ab.transfer_root_dir}",
        f"subscriberFile = {ab.subscriber_file}",
        f"decoderFallBack = {'true' if ab.decoder_fallback else 'false'}",
        f"useEmulator = {'true' if ab.use_emulator else 'false'}",
        f"emulatorAddress = {ab.emulator_address}",
        f"pcmPort = {ab.pcm_port}",
        "",
        "[AMBE_AUDIO]",
        "; Analog_Bridge sends encoded DMR/TLV to the app on txPort.",
        "; Analog_Bridge listens for encoded DMR/TLV from the app on rxPort.",
        f"address = {plan.host}",
        f"txPort = {plan.app_tlv_rx_port}",
        f"rxPort = {plan.app_tlv_tx_port}",
        "ambeMode = DMR",
        f"minTxTimeMS = {ab.min_tx_time_ms}",
        f"gatewayDmrId = {cfg.identity.fallback_source_id}",
        f"repeaterID = {ab.repeater_id}",
        f"txTg = {ob.fixed_tgid}",
        f"txTs = {ab.tx_ts}",
        f"colorCode = {ab.color_code}",
        "",
        "[USRP]",
        "; Analog_Bridge sends decoded PCM to the app on txPort.",
        "; Analog_Bridge listens for PCM from the app on rxPort.",
        f"address = {plan.host}",
        f"txPort = {plan.app_usrp_rx_port}",
        f"rxPort = {plan.app_usrp_tx_port}",
        f"usrpAudio = {ab.usrp_audio}",
        f"usrpGain = {ab.usrp_gain}",
        f"usrpAGC = {ab.usrp_agc}",
        f"tlvAudio = {ab.tlv_audio}",
        f"tlvGain = {ab.tlv_gain}",
        "",
        "[MACROS]",
        "",
    ]

    # When the MD380 emulator is selected, do not emit a DV3000 stanza.
    # Some Analog_Bridge builds still probe/use DV3000/AMBEServer when that
    # stanza is present, even with useEmulator=true.  On slow remote
    # AMBEServer paths this stretches 20 ms PCM blocks to roughly 42-44 ms
    # and causes choppy audio plus OpenBridge under-run/key-drop symptoms.
    if not ab.use_emulator:
        lines.extend(
            [
                "[DV3000]",
                f"address = {cfg.ambeserver.host}",
                f"rxPort = {cfg.ambeserver.port}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "; DV3000 stanza omitted because [analog_bridge].use_emulator=true",
                "; Analog_Bridge should use emulatorAddress instead of AMBEServer/DV3000.",
                "",
            ]
        )
    return "\n".join(lines)


def write_analog_bridge_ini(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and validate echolink-ob/Analog_Bridge UDP port alignment")
    parser.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    parser.add_argument("--write", action="store_true", help="write port state and Analog_Bridge.ini")
    parser.add_argument("--print-ini", action="store_true", help="print generated Analog_Bridge.ini")
    parser.add_argument("--no-reuse-state", action="store_true", help="ignore existing saved port allocation and choose/check again")
    parser.add_argument("--allow-in-use", action="store_true", help="do not fail when planned ports are currently bound; for inspection only")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    try:
        result = build_port_plan(cfg, allow_in_use=args.allow_in_use, reuse_state=not args.no_reuse_state)
    except PortPlanError as exc:
        print(f"ERROR: {exc}")
        return 2

    ini_text = render_analog_bridge_ini(cfg, result.plan)
    if args.write:
        write_state_file(cfg.port_manager.state_file, result)
        write_analog_bridge_ini(result.plan.analog_bridge_ini_path, ini_text)

    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    if args.print_ini:
        print("\n--- Analog_Bridge.ini ---")
        print(ini_text)
    if args.write:
        print(f"port_state={cfg.port_manager.state_file}")
        print(f"analog_bridge_ini={result.plan.analog_bridge_ini_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
