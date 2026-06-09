from pathlib import Path

from echolink_ob.config import load_config


def test_sample_config_loads():
    cfg = load_config("config/config-sample.toml")
    assert cfg.openbridge.host == "hblink.example.net"
    assert cfg.openbridge.port == 62035
    assert cfg.openbridge.local_bind_port == 54015
    assert cfg.openbridge.passphrase == b"CHANGE_ME"
    assert cfg.openbridge.network_id == 310999901
    assert cfg.openbridge.fixed_tgid == 3100
    assert cfg.echolink.callsign == "W1ABC-L"
    assert cfg.echolink.password == "CHANGE_ME"
    assert cfg.echolink.max_connected_stations == 50
    assert cfg.conference.always_repeat_echolink_audio is True
    assert cfg.identity.fallback_source_id == 3109999
    assert cfg.ambeserver.host == "127.0.0.1"
    assert cfg.ambeserver.port == 2460


def test_installed_private_config_can_load_when_copied(tmp_path: Path):
    sample = Path("config/config-sample.toml").read_text(encoding="utf-8")
    private = tmp_path / "config.toml"
    private.write_text(sample, encoding="utf-8")
    cfg = load_config(private)
    assert cfg.echolink.max_connected_stations == 50
    assert cfg.openbridge.local_bind_port == 54015


def test_user_facing_config_does_not_expose_dynamic_analog_ports():
    text = Path("config/config-sample.toml").read_text(encoding="utf-8")
    assert "app_usrp_rx_port" not in text
    assert "app_usrp_tx_port" not in text
    assert "app_tlv_rx_port" not in text
    assert "app_tlv_tx_port" not in text
