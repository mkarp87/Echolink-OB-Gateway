from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from echolink_ob.config import AppConfig

log = logging.getLogger(__name__)


@dataclass
class ManagedMd380Emu:
    """Application-managed md380-emu process.

    The gateway can use a dedicated md380-emu instance for Analog_Bridge.  This
    manager starts that instance before Analog_Bridge and stops only the process
    it started when the gateway exits.  If the configured UDP port is already in
    use, the manager assumes an external md380-emu/service owns it and leaves it
    alone.
    """

    cfg: AppConfig
    process: subprocess.Popen[bytes] | None = None
    reused_existing: bool = False
    started_by_app: bool = False
    last_error: str = ""

    @property
    def enabled(self) -> bool:
        md = self.cfg.md380emu
        return bool(
            self.cfg.analog_bridge.enabled
            and self.cfg.analog_bridge.use_emulator
            and md.enabled
            and md.auto_start
        )

    @property
    def endpoint(self) -> str:
        return f"{self.cfg.md380emu.host}:{self.cfg.md380emu.port}"

    def _is_local_host(self) -> bool:
        host = self.cfg.md380emu.host.strip().lower()
        return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    @staticmethod
    def _udp_port_in_use(host: str, port: int) -> bool:
        bind_host = "127.0.0.1" if host in ("localhost", "0.0.0.0", "::1") else host
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((bind_host, int(port)))
            return False
        except OSError:
            return True
        finally:
            sock.close()

    def start(self) -> None:
        if not self.enabled:
            log.info("md380emu_autostart_disabled endpoint=%s", self.endpoint)
            return
        if not self._is_local_host():
            raise RuntimeError(
                "md380emu.auto_start requires a local md380emu.host; "
                f"got {self.cfg.md380emu.host!r}"
            )

        if self._udp_port_in_use(self.cfg.md380emu.host, self.cfg.md380emu.port):
            self.reused_existing = bool(self.cfg.md380emu.reuse_existing)
            if self.reused_existing:
                log.info("md380emu_reuse_existing endpoint=%s", self.endpoint)
                return
            raise RuntimeError(f"md380emu UDP port already in use: {self.endpoint}")

        qemu = Path(self.cfg.md380emu.qemu_path)
        binary = Path(self.cfg.md380emu.binary_path)
        if not qemu.exists() or not os.access(qemu, os.X_OK):
            raise FileNotFoundError(f"md380emu qemu executable not found or not executable: {qemu}")
        if not binary.exists() or not os.access(binary, os.X_OK):
            raise FileNotFoundError(f"md380emu binary not found or not executable: {binary}")

        cmd = [str(qemu), str(binary), "-S", str(int(self.cfg.md380emu.port))]
        log.info("md380emu_launch command=%r endpoint=%s", cmd, self.endpoint)
        self.process = subprocess.Popen(cmd)
        self.started_by_app = True

        deadline = time.monotonic() + max(0.2, float(self.cfg.md380emu.startup_wait_seconds))
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.last_error = f"md380emu exited early with status {self.process.returncode}"
                raise RuntimeError(self.last_error)
            if self._udp_port_in_use(self.cfg.md380emu.host, self.cfg.md380emu.port):
                log.info("md380emu_started pid=%s endpoint=%s", self.process.pid, self.endpoint)
                return
            time.sleep(0.05)

        # Some md380-emu builds are not visible in ss/bind checks until their
        # first UDP exchange.  Keep the process if it is alive, but make the
        # condition visible in logs/status.
        if self.process.poll() is None:
            log.warning("md380emu_started_without_visible_udp pid=%s endpoint=%s", self.process.pid, self.endpoint)
            return
        self.last_error = f"md380emu exited with status {self.process.returncode}"
        raise RuntimeError(self.last_error)

    def stop(self, *, timeout: float = 5.0) -> None:
        if self.process is None or not self.started_by_app:
            return
        if self.process.poll() is not None:
            return
        log.info("md380emu_terminate pid=%s endpoint=%s", self.process.pid, self.endpoint)
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("md380emu_kill pid=%s endpoint=%s", self.process.pid, self.endpoint)
            self.process.kill()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass

    def snapshot(self) -> dict[str, object]:
        running = False
        pid: int | None = None
        returncode: int | None = None
        if self.process is not None:
            pid = self.process.pid
            returncode = self.process.poll()
            running = returncode is None
        return {
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "auto_start": bool(self.cfg.md380emu.auto_start),
            "started_by_app": self.started_by_app,
            "reused_existing": self.reused_existing,
            "pid": pid,
            "running": running or self.reused_existing,
            "returncode": returncode,
            "last_error": self.last_error,
        }
