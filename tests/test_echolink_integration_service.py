from pathlib import Path
import shutil

from echolink_ob.echolink.integration_selftest import run_selftest


def test_echolink_integration_selftest(tmp_path):
    src = Path('config/config-sample.toml')
    cfg = tmp_path / 'config.toml'
    text = src.read_text()
    text = text.replace('state_file = "/opt/echolink-ob/data/port-plan.json"', f'state_file = "{tmp_path}/port-plan.json"')
    text = text.replace('ini_path = "/opt/echolink-ob/generated/Analog_Bridge.ini"', f'ini_path = "{tmp_path}/Analog_Bridge.ini"')
    # Use high, likely-free local test ports for this test.
    text = text.replace('audio_port = 5198', 'audio_port = 55198')
    text = text.replace('control_port = 5199', 'control_port = 55199')
    text = text.replace('range_start = 33000', 'range_start = 35200')
    text = text.replace('range_end = 33199', 'range_end = 35299')
    cfg.write_text(text)
    report = run_selftest(str(cfg), str(tmp_path / 'out'))
    assert report['ok'] is True
    assert report['stats']['stations_connected'] >= 1
    assert report['stats']['gsm_packets_decoded'] >= 1
    assert report['stats']['pcm_frames_to_usrp'] >= 1



def test_echolink_text_identity_connects_station(tmp_path):
    from echolink_ob.config import load_config
    from echolink_ob.echolink.rtp import build_ndata_info
    from echolink_ob.echolink.service import EchoLinkUdpConferenceService

    src = Path('config/config-sample.toml')
    cfg_path = tmp_path / 'config.toml'
    text = src.read_text()
    text = text.replace('state_file = "/opt/echolink-ob/data/port-plan.json"', f'state_file = "{tmp_path}/port-plan.json"')
    text = text.replace('ini_path = "/opt/echolink-ob/generated/Analog_Bridge.ini"', f'ini_path = "{tmp_path}/Analog_Bridge.ini"')
    text = text.replace('audio_port = 5198', 'audio_port = 55298')
    text = text.replace('control_port = 5199', 'control_port = 55299')
    cfg_path.write_text(text)
    cfg = load_config(cfg_path)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / 'status.json')
    try:
        svc._handle_audio(build_ndata_info("Station KN4KCW\n\nMichael\n\nGreenville NC\n\niPhone"), ("127.0.0.1", 5198))
        assert "KN4KCW" in svc.conference.stations
        assert svc.stats.stations_connected == 1
    finally:
        svc.close()


def test_echolink_goodbye_from_audio_disconnects_station(tmp_path):
    from echolink_ob.config import load_config
    from echolink_ob.echolink.rtp import build_ndata_info
    from echolink_ob.echolink.service import EchoLinkUdpConferenceService

    src = Path('config/config-sample.toml')
    cfg_path = tmp_path / 'config.toml'
    text = src.read_text()
    text = text.replace('state_file = "/opt/echolink-ob/data/port-plan.json"', f'state_file = "{tmp_path}/port-plan.json"')
    text = text.replace('ini_path = "/opt/echolink-ob/generated/Analog_Bridge.ini"', f'ini_path = "{tmp_path}/Analog_Bridge.ini"')
    text = text.replace('audio_port = 5198', 'audio_port = 55398')
    text = text.replace('control_port = 5199', 'control_port = 55399')
    cfg_path.write_text(text)
    cfg = load_config(cfg_path)
    status_file = tmp_path / 'status.json'
    svc = EchoLinkUdpConferenceService(cfg, status_file=status_file)
    try:
        addr = ("127.0.0.1", 5198)
        svc._handle_audio(build_ndata_info("Station KN4KCW\n\nMichael\n\nGreenville NC\n\niPhone"), addr)
        assert "KN4KCW" in svc.conference.stations
        svc._handle_audio(build_ndata_info("Goodbye"), addr)
        assert "KN4KCW" not in svc.conference.stations
        assert svc.stats.stations_disconnected == 1
        assert status_file.exists()
    finally:
        svc.close()
