from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from echolink_ob.analog.ports import build_port_plan, render_analog_bridge_ini, write_analog_bridge_ini, write_state_file
from echolink_ob.bridge.runtime import (
    AnalogOpenBridgeRuntime,
    calibrate_tlv_codec_format,
    maybe_launch_analog_bridge,
    resolve_source_id,
)
from echolink_ob.config import AppConfig, load_config
from echolink_ob.echolink.directory import EchoLinkDirectoryClient, DirectoryCommandResult
from echolink_ob.echolink.service import EchoLinkUdpConferenceService, require_gsm
from echolink_ob.dashboard.server import DashboardServerThread
from echolink_ob.dashboard.lastheard import LastHeardStore
from echolink_ob.dashboard.control import read_commands, remove_commands, append_unique_line
from echolink_ob.identity.radioid import RadioIdIndex
from echolink_ob.logging_setup import setup_logging
from echolink_ob.analog.tone_openbridge import resolve_template_path
from echolink_ob.openbridge.analyzer import read_capture
from echolink_ob.openbridge.dmrd import MAX_3BYTE_ID
from echolink_ob.vocoder.md380emu_process import ManagedMd380Emu

log = logging.getLogger(__name__)


class DirectoryRegistrationThread:
    def __init__(self, cfg: AppConfig, *, refresh_seconds: int = 480, enabled: bool = True) -> None:
        self.cfg = cfg
        self.refresh_seconds = max(60, int(refresh_seconds))
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_result: DirectoryCommandResult | None = None
        self.online_ok = False

    def start(self) -> None:
        if not self.enabled:
            log.info("echolink_directory_disabled")
            return
        self.thread = threading.Thread(target=self._loop, name="echolink-directory", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        client = EchoLinkDirectoryClient(self.cfg)
        while not self.stop_event.is_set():
            result = client.send_status("online")
            self.last_result = result
            self.online_ok = result.ok
            self.stop_event.wait(self.refresh_seconds)
        try:
            self.last_result = client.send_status("offline")
        except Exception as exc:
            log.warning("echolink_directory_offline_failed error=%s", exc)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=8.0)

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "online_ok": self.online_ok,
            "refresh_seconds": self.refresh_seconds,
            "last_result": self.last_result.to_dict() if self.last_result else None,
        }


class FullRuntime:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        template_path: Path,
        template_packets,
        template_source: str,
        source_id: int,
        start_analog_bridge: bool,
        analog_bridge_bin: str | None,
        status_file: str | Path,
        bridge_status_file: str | Path,
        echolink_status_file: str | Path,
        prebuffer_ms: int = 180,
        packet_ms: float = 60.0,
        status_seconds: float = 15.0,
        startup_mute_ms: int = 2500,
        tlv_calibration_ms: int = 0,
        directory_enabled: bool = True,
    ) -> None:
        self.cfg = cfg
        self.template_path = template_path
        self.template_packets = template_packets
        self.template_source = template_source
        self.source_id = source_id
        self.start_analog_bridge = start_analog_bridge
        self.analog_bridge_bin = analog_bridge_bin
        self.status_file = Path(status_file)
        self.bridge_status_file = Path(bridge_status_file)
        self.echolink_status_file = Path(echolink_status_file)
        self.prebuffer_ms = prebuffer_ms
        self.packet_ms = packet_ms
        self.status_seconds = max(1.0, status_seconds)
        self.startup_mute_ms = startup_mute_ms
        self.tlv_calibration_ms = tlv_calibration_ms
        self.directory_enabled = directory_enabled
        self.stop_event = threading.Event()
        self.analog_proc: subprocess.Popen[bytes] | None = None
        self.md380emu = ManagedMd380Emu(cfg)
        self.bridge_runtime: AnalogOpenBridgeRuntime | None = None
        self.echolink_service: EchoLinkUdpConferenceService | None = None
        self.directory = DirectoryRegistrationThread(
            cfg,
            refresh_seconds=cfg.echolink.directory_refresh_seconds,
            enabled=directory_enabled and cfg.echolink.register_with_directory,
        )
        self.last_heard = LastHeardStore(cfg.dashboard.last_heard_file, max_records=cfg.dashboard.last_heard_limit)
        self.radioid = RadioIdIndex.from_file(cfg.identity.radioid_file)
        self.dashboard: DashboardServerThread | None = None
        self.dashboard_thread: threading.Thread | None = None
        self.threads: list[threading.Thread] = []
        self.started_at = time.monotonic()
        self.started_at_wall = time.time()
        self._processed_dashboard_commands: set[str] = set()
        self.exit_code = 0

    def _build_plan(self):
        result = build_port_plan(self.cfg, allow_in_use=True, reuse_state=True)
        write_state_file(self.cfg.port_manager.state_file, result)
        write_analog_bridge_ini(result.plan.analog_bridge_ini_path, render_analog_bridge_ini(self.cfg, result.plan))
        return result

    def setup_report(self) -> dict[str, object]:
        result = build_port_plan(self.cfg, allow_in_use=True, reuse_state=True)
        return {
            "status": "full_runtime_ready",
            "callsign": self.cfg.echolink.callsign,
            "source_id": self.source_id,
            "template": str(self.template_path),
            "template_source": self.template_source,
            "start_analog_bridge": self.start_analog_bridge,
            "md380emu": self.md380emu.snapshot(),
            "directory_enabled": self.directory.enabled,
            "dashboard_enabled": self.cfg.dashboard.enabled,
            "dashboard_url": f"http://{self.cfg.dashboard.listen_host}:{self.cfg.dashboard.listen_port}/" if self.cfg.dashboard.enabled else None,
            "bridge_status_file": str(self.bridge_status_file),
            "echolink_status_file": str(self.echolink_status_file),
            "status_file": str(self.status_file),
            "port_plan": result.plan.as_dict(),
            "note": "Full runtime starts OpenBridge, Analog_Bridge, and EchoLink UDP conference service. EchoLink directory registration is enabled unless disabled by flag/config.",
        }

    def start(self) -> None:
        require_gsm()
        if self.start_analog_bridge:
            self.md380emu.start()
        result = self._build_plan()
        self.analog_proc = maybe_launch_analog_bridge(
            binary=self.analog_bridge_bin,
            ini_path=result.plan.analog_bridge_ini_path,
            should_start=self.start_analog_bridge,
        )
        if self.analog_proc is not None:
            time.sleep(1.0)
            if self.analog_proc.poll() is not None:
                raise RuntimeError(f"Analog_Bridge exited early with status {self.analog_proc.returncode}")

        tlv_format = None
        if self.tlv_calibration_ms > 0:
            tlv_format = calibrate_tlv_codec_format(
                host=result.plan.host,
                tlv_rx_port=result.plan.app_tlv_rx_port,
                usrp_tx_port=result.plan.app_usrp_tx_port,
                duration_ms=self.tlv_calibration_ms,
            )

        self.bridge_runtime = AnalogOpenBridgeRuntime(
            cfg=self.cfg,
            plan=result.plan,
            template_packets=self.template_packets,
            source_id=self.source_id,
            source_id_provider=self._current_echolink_source_id,
            last_heard_callback=self._record_dmr_last_heard,
            prebuffer_ms=self.prebuffer_ms,
            packet_ms=self.packet_ms,
            tlv_format=tlv_format,
            startup_mute_ms=self.startup_mute_ms,
        )
        self.echolink_service = EchoLinkUdpConferenceService(self.cfg, status_file=self.echolink_status_file)
        if self.cfg.dashboard.enabled:
            self.dashboard = DashboardServerThread(self.cfg)
            self.dashboard_thread = threading.Thread(target=self.dashboard.start, name="dashboard-http", daemon=True)

        self.threads = [
            threading.Thread(
                target=self.bridge_runtime.run,
                kwargs={"duration": None, "status_interval_s": self.status_seconds, "status_file": self.bridge_status_file},
                name="analog-openbridge-runtime",
                daemon=True,
            ),
            threading.Thread(
                target=self.echolink_service.run,
                kwargs={"seconds": None},
                name="echolink-udp-runtime",
                daemon=True,
            ),
        ]
        for th in self.threads:
            th.start()
        if self.dashboard_thread is not None:
            self.dashboard_thread.start()
        self.directory.start()
        log.info("full_runtime_started callsign=%s source_id=%s", self.cfg.echolink.callsign, self.source_id)

    def snapshot(self) -> dict[str, object]:
        bridge = self.bridge_runtime.status_snapshot() if self.bridge_runtime else None
        echolink = None
        if self.echolink_service is not None:
            echolink = {
                "connected_stations": len(self.echolink_service.conference.stations),
                "active_speaker": self.echolink_service.conference.active_speaker,
                "stations": self.echolink_service.connected_station_rows(),
                "stats": self.echolink_service.stats.__dict__,
                "router": self.echolink_service.router.snapshot(),
                "dmr_audio_active": self.echolink_service._dmr_audio_active,
            }
        analog = None
        if self.analog_proc is not None:
            analog = {"pid": self.analog_proc.pid, "running": self.analog_proc.poll() is None}
        return {
            "status": "running" if not self.stop_event.is_set() else "stopping",
            "uptime_seconds": int(time.monotonic() - self.started_at),
            "callsign": self.cfg.echolink.callsign,
            "bridge": bridge,
            "echolink": echolink,
            "directory": self.directory.snapshot(),
            "analog_bridge": analog,
            "md380emu": self.md380emu.snapshot(),
        }


    def _current_echolink_source_id(self) -> int:
        if self.echolink_service is not None:
            return self.echolink_service.current_gateway_source_id()
        return int(self.source_id)

    def _record_dmr_last_heard(self, dmr_id: int) -> None:
        callsign = self.radioid.lookup_id(dmr_id) or str(dmr_id)
        name = self.radioid.lookup_name(dmr_id) or ""
        self.last_heard.record(callsign=callsign, dmr_id=dmr_id, name=name, source="dmr", event="heard")

    def _handle_dashboard_command(self, command) -> None:
        action = command.action
        payload = command.payload
        if action == "disconnect":
            callsign = str(payload.get("callsign", "")).upper().strip()
            if self.echolink_service is not None and callsign:
                ok = self.echolink_service.disconnect_station(callsign, reason="Disconnected by dashboard")
                log.info("dashboard_command_disconnect callsign=%s ok=%s", callsign, ok)
            return
        if action == "block":
            callsign = str(payload.get("callsign", "")).upper().strip()
            if callsign:
                append_unique_line(self.cfg.access.banlist_file, callsign)
                if self.echolink_service is not None:
                    self.echolink_service.reload_access_rules()
                    self.echolink_service.disconnect_station(callsign, reason="Blocked by dashboard")
                log.info("dashboard_command_block callsign=%s", callsign)
            return
        if action == "reload_access":
            if self.echolink_service is not None:
                self.echolink_service.reload_access_rules()
            log.info("dashboard_command_reload_access")
            return
        if action == "reload":
            log.warning("dashboard_command_reload requested")
            self.exit_code = 75
            self.stop_event.set()
            return
        log.warning("dashboard_command_unknown action=%s payload=%s", action, payload)

    def poll_dashboard_commands(self) -> None:
        processed_now: set[str] = set()
        for command in read_commands(self.cfg.dashboard.control_file):
            if not command.command_id or command.command_id in self._processed_dashboard_commands:
                continue

            # Reload/restart is a one-shot command. If a previous version left a
            # reload line in dashboard-commands.jsonl, ignore and remove it on
            # process startup; otherwise systemd restarts into the same command
            # and the service loops forever.
            if command.action == "reload" and command.created_at < self.started_at_wall:
                log.warning(
                    "dashboard_command_reload_stale_ignored command_id=%s created_at=%s started_at=%s",
                    command.command_id,
                    command.created_at,
                    self.started_at_wall,
                )
                self._processed_dashboard_commands.add(command.command_id)
                processed_now.add(command.command_id)
                continue

            self._processed_dashboard_commands.add(command.command_id)
            processed_now.add(command.command_id)
            self._handle_dashboard_command(command)

        if processed_now:
            try:
                removed = remove_commands(self.cfg.dashboard.control_file, processed_now)
                log.debug("dashboard_commands_acknowledged count=%s removed=%s", len(processed_now), removed)
            except Exception as exc:
                log.warning("dashboard_commands_ack_failed error=%s", exc)

    def write_status(self) -> None:
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.status_file.write_text(json.dumps(self.snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def run(self, *, seconds: float | None = None) -> None:
        started = time.monotonic()
        next_status = 0.0
        try:
            self.start()
            while not self.stop_event.is_set():
                if seconds is not None and time.monotonic() - started >= seconds:
                    self.stop_event.set()
                    break
                if self.analog_proc is not None and self.analog_proc.poll() is not None:
                    raise RuntimeError(f"Analog_Bridge exited with status {self.analog_proc.returncode}")
                if self.md380emu.process is not None and self.md380emu.process.poll() is not None:
                    raise RuntimeError(f"md380-emu exited with status {self.md380emu.process.returncode}")
                self.poll_dashboard_commands()
                now = time.monotonic()
                if now >= next_status:
                    snap = self.snapshot()
                    self.write_status()
                    log.info(
                        "full_status uptime=%s echolink_stations=%s bridge_ob_sent=%s bridge_ob_recv=%s directory_online=%s md380emu_running=%s",
                        snap["uptime_seconds"],
                        (snap.get("echolink") or {}).get("connected_stations"),
                        ((snap.get("bridge") or {}).get("stats") or {}).get("openbridge_packets_sent"),
                        ((snap.get("bridge") or {}).get("stats") or {}).get("openbridge_packets_received"),
                        (snap.get("directory") or {}).get("online_ok"),
                        (snap.get("md380emu") or {}).get("running"),
                    )
                    next_status = now + self.status_seconds
                time.sleep(0.5)
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        log.info("full_runtime_stopping")
        self.directory.stop()
        if self.dashboard is not None:
            self.dashboard.stop()
        if self.dashboard_thread is not None:
            self.dashboard_thread.join(timeout=3.0)
        if self.echolink_service is not None:
            # Tell connected EchoLink clients to disconnect before closing UDP
            # sockets. This is best-effort and intentionally happens before
            # request_stop()/close().
            self.echolink_service.disconnect_all(reason="Gateway shutting down")
            self.echolink_service.request_stop()
        if self.bridge_runtime is not None:
            self.bridge_runtime.stop_event.set()
        for th in self.threads:
            th.join(timeout=5.0)
        if self.echolink_service is not None:
            self.echolink_service.close()
        if self.analog_proc is not None and self.analog_proc.poll() is None:
            log.info("analog_bridge_terminate pid=%s", self.analog_proc.pid)
            self.analog_proc.terminate()
            try:
                self.analog_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.analog_proc.kill()
        self.md380emu.stop()
        self.write_status()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the full EchoLink <-> Analog_Bridge <-> OpenBridge gateway")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--seconds", type=float, default=None)
    p.add_argument("--template", default=None)
    p.add_argument("--template-mode", choices=["auto", "capture", "builtin"], default="auto")
    p.add_argument("--source-id", type=int, default=None, help=f"fallback/static DMR source ID, 0..{MAX_3BYTE_ID}")
    p.add_argument("--start-analog-bridge", action="store_true", default=True)
    p.add_argument("--no-start-analog-bridge", action="store_false", dest="start_analog_bridge")
    p.add_argument("--analog-bridge-bin", default=None)
    p.add_argument("--no-directory", action="store_true", help="Do not register with the EchoLink directory server")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status-seconds", type=float, default=15.0)
    p.add_argument("--status-file", default="/opt/echolink-ob/logs/full-status.json")
    p.add_argument("--bridge-status-file", default="/opt/echolink-ob/logs/bridge-status.json")
    p.add_argument("--echolink-status-file", default="/opt/echolink-ob/logs/echolink-status.json")
    p.add_argument("--prebuffer-ms", type=int, default=180)
    p.add_argument("--packet-ms", type=float, default=60.0)
    p.add_argument("--startup-mute-ms", type=int, default=2500)
    p.add_argument("--tlv-calibration-ms", type=int, default=0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    parser = build_parser()
    try:
        source_id = resolve_source_id(cfg, args.source_id, parser)
        template_path, template_source = resolve_template_path(args.template, mode=args.template_mode)
        template_packets = read_capture(template_path, cfg.openbridge.passphrase)
        runtime = FullRuntime(
            cfg,
            template_path=template_path,
            template_packets=template_packets,
            template_source=template_source,
            source_id=source_id,
            start_analog_bridge=args.start_analog_bridge,
            analog_bridge_bin=args.analog_bridge_bin,
            status_file=args.status_file,
            bridge_status_file=args.bridge_status_file,
            echolink_status_file=args.echolink_status_file,
            prebuffer_ms=args.prebuffer_ms,
            packet_ms=args.packet_ms,
            status_seconds=args.status_seconds,
            startup_mute_ms=args.startup_mute_ms,
            tlv_calibration_ms=args.tlv_calibration_ms,
            directory_enabled=not args.no_directory,
        )
        print(json.dumps(runtime.setup_report(), indent=2, sort_keys=True))
        if args.dry_run:
            return 0

        stop_requested = threading.Event()

        def _stop_handler(_signum, _frame):
            stop_requested.set()
            runtime.stop_event.set()

        try:
            signal.signal(signal.SIGTERM, _stop_handler)
            signal.signal(signal.SIGINT, _stop_handler)
        except ValueError:
            pass
        runtime.run(seconds=args.seconds)
        print(json.dumps({"status": "full_runtime_stopped", "snapshot": runtime.snapshot()}, indent=2, sort_keys=True))
        return int(runtime.exit_code)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.exception("full_runtime_failed error=%s", exc)
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
