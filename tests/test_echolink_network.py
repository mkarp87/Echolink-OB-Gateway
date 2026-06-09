from __future__ import annotations

import socket
from pathlib import Path

from echolink_ob.config import load_config
from echolink_ob.echolink.network import run_echolink_preflight


def write_config(tmp_path: Path, audio_port: int, control_port: int) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''
[echolink]
callsign = "W1ABC-L"
password = "secret"
max_connected_stations = 50
bind_host = "127.0.0.1"
audio_port = {audio_port}
control_port = {control_port}
directory_host = "127.0.0.1"
directory_port = 9

[openbridge]
passphrase = "x"

[identity]
fallback_source_id = 1234567
''',
        encoding="utf-8",
    )
    return cfg


def free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_echolink_preflight_udp_ports_available(tmp_path: Path):
    audio = free_udp_port()
    control = free_udp_port()
    cfg = load_config(write_config(tmp_path, audio, control))
    report = run_echolink_preflight(cfg, skip_directory=True)
    assert report.ok is True
    assert [check.available for check in report.udp_checks] == [True, True]
    assert report.directory_check is None


def test_echolink_preflight_detects_busy_udp_port(tmp_path: Path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    busy = sock.getsockname()[1]
    control = free_udp_port()
    try:
        cfg = load_config(write_config(tmp_path, busy, control))
        report = run_echolink_preflight(cfg, skip_directory=True)
        assert report.ok is False
        assert report.udp_checks[0].available is False
        assert report.udp_checks[1].available is True
    finally:
        sock.close()


def test_echolink_config_defaults_have_standard_ports():
    cfg = load_config("config/config-sample.toml")
    assert cfg.echolink.audio_port == 5198
    assert cfg.echolink.control_port == 5199
    assert cfg.echolink.directory_port == 5200
