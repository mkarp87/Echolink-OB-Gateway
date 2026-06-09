from __future__ import annotations

import socket
import threading

from echolink_ob.config import load_config
from echolink_ob.echolink.directory import EchoLinkDirectoryClient, ECHOLOGIN_SEP
from echolink_ob.full_runtime import DirectoryRegistrationThread


def test_directory_command_format_contains_callsign_password_and_status():
    cfg = load_config("config/config-sample.toml")
    cmd = EchoLinkDirectoryClient(cfg).build_command("online")
    assert cmd.startswith(b"lW1ABC-L" + ECHOLOGIN_SEP)
    assert b"CHANGE_ME" in cmd
    assert b"ONLINE3.38(" in cmd
    assert cmd.endswith(b"\r")


def test_directory_send_status_against_fake_server(tmp_path):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = []

    def worker():
        conn, _ = srv.accept()
        with conn:
            received.append(conn.recv(4096))
            conn.sendall(b"OK")
        srv.close()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    cfg = load_config("config/config-sample.toml")
    object.__setattr__(cfg.echolink, "directory_host", "127.0.0.1")
    object.__setattr__(cfg.echolink, "directory_port", port)
    result = EchoLinkDirectoryClient(cfg, timeout_s=1.0).send_status("online")
    th.join(timeout=2.0)
    assert result.ok
    assert received and received[0].startswith(b"lW1ABC-L")


def test_directory_registration_thread_disabled_does_not_start():
    cfg = load_config("config/config-sample.toml")
    reg = DirectoryRegistrationThread(cfg, enabled=False)
    reg.start()
    assert reg.thread is None
    assert reg.snapshot()["enabled"] is False
