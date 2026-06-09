from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from echolink_ob.analog.ports import (
    PortPlanError,
    analog_bridge_mapping,
    build_port_plan,
    render_analog_bridge_ini,
    write_state_file,
)
from echolink_ob.config import load_config


def write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''
[openbridge]
local_bind_port = 54015
fixed_tgid = 310001

[identity]
fallback_source_id = 1234567

[ambeserver]
host = "192.0.2.10"
port = 2460

[md380emu]
port = 2470

[port_manager]
host = "127.0.0.1"
range_start = 45000
range_end = 45020
reserved_ports = [2222, 2460, 2470, 54015]
state_file = "{tmp_path / 'port-plan.json'}"
reuse_existing_allocation = true

[analog_bridge]
ini_path = "{tmp_path / 'Analog_Bridge.ini'}"
app_usrp_rx_port = "auto"
app_usrp_tx_port = "auto"
app_tlv_rx_port = "auto"
app_tlv_tx_port = "auto"
repeater_id = 31000190
tx_ts = 2
color_code = 1
''',
        encoding="utf-8",
    )
    return cfg


def test_auto_port_plan_chooses_unique_range_ports(tmp_path: Path):
    cfg = load_config(write_config(tmp_path))
    result = build_port_plan(cfg, reuse_state=False)
    plan = result.plan

    assert len(set(plan.ports)) == 4
    assert all(45000 <= p <= 45020 for p in plan.ports)
    assert 54015 not in plan.ports
    assert all(c.available for c in result.checks)

    mapping = analog_bridge_mapping(plan)
    assert mapping["USRP"]["txPort"] == plan.app_usrp_rx_port
    assert mapping["USRP"]["rxPort"] == plan.app_usrp_tx_port
    assert mapping["AMBE_AUDIO"]["txPort"] == plan.app_tlv_rx_port
    assert mapping["AMBE_AUDIO"]["rxPort"] == plan.app_tlv_tx_port


def test_analog_bridge_ini_renders_matching_tx_rx_ports(tmp_path: Path):
    cfg = load_config(write_config(tmp_path))
    result = build_port_plan(cfg, reuse_state=False)
    ini = render_analog_bridge_ini(cfg, result.plan)

    assert "[USRP]" in ini
    assert "[AMBE_AUDIO]" in ini
    assert f"txPort = {result.plan.app_usrp_rx_port}" in ini
    assert f"rxPort = {result.plan.app_usrp_tx_port}" in ini
    assert f"txPort = {result.plan.app_tlv_rx_port}" in ini
    assert f"rxPort = {result.plan.app_tlv_tx_port}" in ini
    assert "address = 192.0.2.10" in ini
    assert "txTg = 310001" in ini
    assert "gatewayDmrId = 1234567" in ini


def test_state_file_can_be_reused(tmp_path: Path):
    cfg = load_config(write_config(tmp_path))
    first = build_port_plan(cfg, reuse_state=False)
    write_state_file(cfg.port_manager.state_file, first)

    second = build_port_plan(cfg, reuse_state=True)
    assert second.reused_state is True
    assert second.plan.ports == first.plan.ports

    state = json.loads(Path(cfg.port_manager.state_file).read_text(encoding="utf-8"))
    assert state["plan"]["app_usrp_rx_port"] == first.plan.app_usrp_rx_port


def test_duplicate_explicit_ports_are_rejected(tmp_path: Path):
    cfg_path = tmp_path / "duplicate.toml"
    cfg_path.write_text(
        f'''
[openbridge]
local_bind_port = 54015
fixed_tgid = 310001

[identity]
fallback_source_id = 1234567

[ambeserver]
port = 2460

[md380emu]
port = 2470

[port_manager]
host = "127.0.0.1"
range_start = 45000
range_end = 45020
reserved_ports = []
state_file = "{tmp_path / 'duplicate-plan.json'}"

[analog_bridge]
ini_path = "{tmp_path / 'Analog_Bridge.ini'}"
app_usrp_rx_port = 45001
app_usrp_tx_port = 45001
app_tlv_rx_port = 45002
app_tlv_tx_port = 45003
''',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    with pytest.raises(PortPlanError, match="unique"):
        build_port_plan(cfg, reuse_state=False)


def test_explicit_occupied_port_is_rejected(tmp_path: Path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    occupied = sock.getsockname()[1]
    start = max(1, occupied - 20)
    end = min(65535, occupied + 20)
    other_ports = [p for p in range(start, end + 1) if p != occupied][:3]
    assert len(other_ports) == 3
    try:
        cfg_path = tmp_path / "occupied.toml"
        cfg_path.write_text(
            f'''
[openbridge]
local_bind_port = 54015
fixed_tgid = 310001

[identity]
fallback_source_id = 1234567

[ambeserver]
port = 2460

[md380emu]
port = 2470

[port_manager]
host = "127.0.0.1"
range_start = {start}
range_end = {end}
reserved_ports = []
state_file = "{tmp_path / 'occupied-plan.json'}"

[analog_bridge]
ini_path = "{tmp_path / 'Analog_Bridge.ini'}"
app_usrp_rx_port = {occupied}
app_usrp_tx_port = {other_ports[0]}
app_tlv_rx_port = {other_ports[1]}
app_tlv_tx_port = {other_ports[2]}
''',
            encoding="utf-8",
        )
        cfg = load_config(cfg_path)
        with pytest.raises(PortPlanError, match="already in use"):
            build_port_plan(cfg, reuse_state=False)
    finally:
        sock.close()


def test_analog_bridge_ini_omits_dv3000_when_emulator_enabled(tmp_path: Path):
    cfg_path = write_config(tmp_path)
    text = cfg_path.read_text(encoding='utf-8')
    text = text.replace('color_code = 1\n', 'color_code = 1\nuse_emulator = true\nemulator_address = "127.0.0.1:2990"\n')
    cfg_path.write_text(text, encoding='utf-8')

    cfg = load_config(cfg_path)
    result = build_port_plan(cfg, reuse_state=False)
    ini = render_analog_bridge_ini(cfg, result.plan)

    assert 'useEmulator = true' in ini
    assert 'emulatorAddress = 127.0.0.1:2990' in ini
    assert '[DV3000]' not in ini
    assert '192.0.2.10' not in ini
    assert 'DV3000 stanza omitted' in ini
