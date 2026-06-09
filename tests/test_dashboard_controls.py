import json
from pathlib import Path

from echolink_ob.dashboard.control import append_command, append_unique_line, read_commands
from echolink_ob.dashboard.lastheard import LastHeardStore


def test_lastheard_dedups_by_callsign_and_tracks_tx(tmp_path: Path):
    store = LastHeardStore(tmp_path / "lastheard.json")
    store.record(callsign="K1ABC", dmr_id=123, name="One", source="echolink", event="connected")
    store.record(callsign="K1ABC", dmr_id=123, name="One", source="echolink", event="heard")
    store.record(callsign="K1ABC", dmr_id=123, name="One", source="echolink", event="connected")
    rows = store.recent_connections(20)
    assert len(rows) == 1
    assert rows[0].callsign == "K1ABC"
    assert rows[0].connect_count == 2
    assert rows[0].tx_count == 1
    assert rows[0].last_connect_utc
    assert rows[0].last_tx_utc


def test_dashboard_command_queue_and_unique_banlist(tmp_path: Path):
    q = tmp_path / "commands.jsonl"
    cmd = append_command(q, "disconnect", callsign="K1ABC")
    commands = read_commands(q)
    assert commands[0].command_id == cmd.command_id
    assert commands[0].action == "disconnect"
    assert commands[0].payload["callsign"] == "K1ABC"

    ban = tmp_path / "banlist.txt"
    assert append_unique_line(ban, "k1abc") is True
    assert append_unique_line(ban, "K1ABC") is False
    assert ban.read_text().strip() == "K1ABC"

from types import SimpleNamespace
import time

from echolink_ob.dashboard.control import remove_commands
from echolink_ob.full_runtime import FullRuntime


def test_remove_commands_acknowledges_processed_entries(tmp_path: Path):
    q = tmp_path / "commands.jsonl"
    first = append_command(q, "disconnect", callsign="K1ABC")
    second = append_command(q, "reload")

    removed = remove_commands(q, {first.command_id})

    assert removed == 1
    remaining = read_commands(q)
    assert [cmd.command_id for cmd in remaining] == [second.command_id]


def test_runtime_ignores_and_removes_stale_reload_command(tmp_path: Path):
    q = tmp_path / "commands.jsonl"
    old_reload = append_command(q, "reload")

    runtime = FullRuntime.__new__(FullRuntime)
    runtime.cfg = SimpleNamespace(dashboard=SimpleNamespace(control_file=str(q)))
    runtime.started_at_wall = time.time() + 1.0
    runtime._processed_dashboard_commands = set()
    handled: list[str] = []
    runtime._handle_dashboard_command = lambda command: handled.append(command.action)

    FullRuntime.poll_dashboard_commands(runtime)

    assert handled == []
    assert old_reload.command_id in runtime._processed_dashboard_commands
    assert read_commands(q) == []


def test_runtime_processes_and_removes_fresh_reload_command(tmp_path: Path):
    q = tmp_path / "commands.jsonl"

    runtime = FullRuntime.__new__(FullRuntime)
    runtime.cfg = SimpleNamespace(dashboard=SimpleNamespace(control_file=str(q)))
    runtime.started_at_wall = time.time() - 1.0
    runtime._processed_dashboard_commands = set()
    handled: list[str] = []
    runtime._handle_dashboard_command = lambda command: handled.append(command.action)

    fresh_reload = append_command(q, "reload")
    FullRuntime.poll_dashboard_commands(runtime)

    assert handled == ["reload"]
    assert fresh_reload.command_id in runtime._processed_dashboard_commands
    assert read_commands(q) == []
