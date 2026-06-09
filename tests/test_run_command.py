from echolink_ob.run import main


def test_run_preflight_no_write(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
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
range_start = 45100
range_end = 45120
reserved_ports = [2222, 2460, 2470, 54015]
state_file = "{tmp_path / 'port-plan.json'}"
reuse_existing_allocation = false

[analog_bridge]
ini_path = "{tmp_path / 'Analog_Bridge.ini'}"
''',
        encoding="utf-8",
    )
    assert main(["--config", str(cfg), "--no-write-analog-config"]) == 0
