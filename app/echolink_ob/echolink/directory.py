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

ECHOLOGIN_SEP = b"\xac\xac"


@dataclass(frozen=True)
class DirectoryCommandResult:
    ok: bool
    status: str
    server: str
    port: int
    elapsed_ms: int
    reply: str
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class EchoLinkDirectoryClient:
    """Minimal EchoLink directory registration client.

    This implements the status registration command format used by EchoLink-
    compatible implementations such as SvxLink's EchoLib.  It is intentionally
    small: the UDP conference service handles station audio/control, and this
    client only marks the node online/busy/offline at the directory server.
    """

    def __init__(self, cfg: AppConfig, *, timeout_s: float = 5.0) -> None:
        self.cfg = cfg
        self.timeout_s = timeout_s

    def _local_time(self) -> str:
        return time.strftime("%H:%M", time.localtime())

    def _status_line(self, status: str) -> str:
        status = status.lower().strip()
        if status == "online":
            return f"ONLINE3.38({self._local_time()})"
        if status == "busy":
            return f"BUSY3.40({self._local_time()})"
        if status == "offline":
            return "OFF-V3.40"
        raise ValueError(f"unsupported directory status: {status}")

    def build_command(self, status: str) -> bytes:
        callsign = self.cfg.echolink.callsign.upper().strip()
        password = self.cfg.echolink.password.strip()
        description = self.cfg.echolink.location.strip() or self.cfg.echolink.status_text.strip()
        payload = b"l" + callsign.encode("ascii", "replace") + ECHOLOGIN_SEP + password.encode("ascii", "replace")
        payload += b"\r" + self._status_line(status).encode("ascii", "replace")
        payload += b"\r" + description[:80].encode("ascii", "replace") + b"\r"
        return payload

    def send_status(self, status: str) -> DirectoryCommandResult:
        start = time.monotonic()
        server = self.cfg.echolink.directory_host
        port = self.cfg.echolink.directory_port
        try:
            cmd = self.build_command(status)
            with socket.create_connection((server, port), timeout=self.timeout_s) as sock:
                sock.settimeout(self.timeout_s)
                sock.sendall(cmd)
                try:
                    reply = sock.recv(1024)
                except socket.timeout:
                    reply = b""
            elapsed = int((time.monotonic() - start) * 1000)
            text = reply.decode("latin1", "replace")
            ok = text.startswith("OK")
            if ok:
                log.info("echolink_directory_status_ok status=%s server=%s:%s elapsed_ms=%s", status, server, port, elapsed)
            else:
                log.warning("echolink_directory_status_unexpected status=%s server=%s:%s reply=%r", status, server, port, text[:120])
            return DirectoryCommandResult(ok=ok, status=status, server=server, port=port, elapsed_ms=elapsed, reply=text)
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            log.warning("echolink_directory_status_failed status=%s server=%s:%s error=%s", status, server, port, exc)
            return DirectoryCommandResult(ok=False, status=status, server=server, port=port, elapsed_ms=elapsed, reply="", error=str(exc))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Set EchoLink directory status")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--status", choices=["online", "busy", "offline"], default="online")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--output", default="/opt/echolink-ob/diagnostics/echolink-directory.json")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    result = EchoLinkDirectoryClient(cfg, timeout_s=args.timeout).send_status(args.status)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    print(f"report={output}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
