from __future__ import annotations

import os
import socket
import time
from pathlib import Path

from echolink_ob.config import load_config
from echolink_ob.vocoder.md380emu_process import ManagedMd380Emu


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _config_text(tmp_path: Path, *, port: int, qemu: Path, emu: Path) -> str:
    text = Path("config/config-sample.toml").read_text(encoding="utf-8")
    text = text.replace('state_file = "/opt/echolink-ob/data/port-plan.json"', f'state_file = "{tmp_path}/port-plan.json"')
    text = text.replace('ini_path = "/opt/echolink-ob/generated/Analog_Bridge.ini"', f'ini_path = "{tmp_path}/Analog_Bridge.ini"')
    text = text.replace('port = 2990', f'port = {port}', 1)
    text = text.replace('auto_start = true', 'auto_start = true', 1)
    text = text.replace('reuse_existing = true', 'reuse_existing = true', 1)
    text = text.replace('qemu_path = "/opt/md380-emu/qemu-arm-static"', f'qemu_path = "{qemu}"', 1)
    text = text.replace('binary_path = "/opt/md380-emu/md380-emu"', f'binary_path = "{emu}"', 1)
    text = text.replace('startup_wait_seconds = 2.0', 'startup_wait_seconds = 1.0', 1)
    text = text.replace('emulator_address = "127.0.0.1:2990"', f'emulator_address = "127.0.0.1:{port}"', 1)
    return text


def test_managed_md380emu_starts_and_stops_fake_process(tmp_path):
    port = _free_udp_port()
    qemu = tmp_path / "fake-qemu"
    emu = tmp_path / "fake-md380-emu"
    qemu.write_text(
        "#!/usr/bin/env python3\n"
        "import socket, sys, time\n"
        "port = int(sys.argv[-1])\n"
        "sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "sock.bind(('127.0.0.1', port))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    emu.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(qemu, 0o755)
    os.chmod(emu, 0o755)

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_config_text(tmp_path, port=port, qemu=qemu, emu=emu), encoding="utf-8")
    cfg = load_config(cfg_path)
    manager = ManagedMd380Emu(cfg)

    manager.start()
    try:
        assert manager.started_by_app is True
        assert manager.process is not None
        assert manager.process.poll() is None
        assert manager.snapshot()["running"] is True
    finally:
        manager.stop(timeout=1.0)
    time.sleep(0.05)
    assert manager.process is not None
    assert manager.process.poll() is not None


def test_managed_md380emu_reuses_existing_udp_listener(tmp_path):
    port = _free_udp_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    try:
        qemu = tmp_path / "fake-qemu"
        emu = tmp_path / "fake-md380-emu"
        qemu.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        emu.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(qemu, 0o755)
        os.chmod(emu, 0o755)
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(_config_text(tmp_path, port=port, qemu=qemu, emu=emu), encoding="utf-8")
        manager = ManagedMd380Emu(load_config(cfg_path))
        manager.start()
        assert manager.reused_existing is True
        assert manager.process is None
        assert manager.snapshot()["running"] is True
        manager.stop()
    finally:
        sock.close()
