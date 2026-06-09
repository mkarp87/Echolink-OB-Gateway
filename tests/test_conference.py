import pytest

from echolink_ob.echolink.conference import EchoLinkConferenceManager
from echolink_ob.echolink.station import EchoLinkStation
from echolink_ob.identity.callsign import normalize_echolink_callsign


def st(cs):
    return EchoLinkStation(cs, normalize_echolink_callsign(cs))


def test_conference_accepts_50_stations():
    conf = EchoLinkConferenceManager(max_stations=50)
    for i in range(50):
        conf.add_station(st(f"N{i:04d}"))
    assert len(conf.stations) == 50
    with pytest.raises(RuntimeError):
        conf.add_station(st("OVERFLOW"))


def test_echolink_audio_always_repeats_to_others_not_speaker():
    conf = EchoLinkConferenceManager(max_stations=50)
    for cs in ["K1ABC-L", "N2XYZ", "W3AAA-R"]:
        conf.add_station(st(cs))
    delivery = conf.route_speaker_audio("K1ABC-L", b"abc", gateway_allowed=False)
    assert delivery.speaker == "K1ABC-L"
    assert sorted(delivery.recipients) == ["N2XYZ", "W3AAA-R"]
    assert delivery.gateway_allowed is False


def test_second_speaker_blocked_while_first_active():
    conf = EchoLinkConferenceManager(max_stations=50)
    conf.add_station(st("K1ABC-L"))
    conf.add_station(st("N2XYZ"))
    one = conf.route_speaker_audio("K1ABC-L", b"1", gateway_allowed=True)
    two = conf.route_speaker_audio("N2XYZ", b"2", gateway_allowed=True)
    assert one.recipients == ["N2XYZ"]
    assert two.recipients == []
    assert two.gateway_allowed is False


def test_dmr_broadcast_goes_to_all():
    conf = EchoLinkConferenceManager(max_stations=50)
    for cs in ["A", "B", "C"]:
        conf.add_station(st(cs))
    delivery = conf.broadcast_from_dmr(b"x")
    assert sorted(delivery.recipients) == ["A", "B", "C"]


def test_active_speaker_released_after_idle_timeout():
    import time
    conf = EchoLinkConferenceManager(max_stations=50)
    conf.add_station(st("KE4TZN"))
    conf.add_station(st("KN4KCW"))
    first = conf.route_speaker_audio("KE4TZN", b"1", gateway_allowed=True)
    assert first.gateway_allowed is True
    assert conf.active_speaker == "KE4TZN"
    conf.stations["KE4TZN"].last_heard_at = time.monotonic() - 2.0
    assert conf.release_inactive_speaker(0.7) is True
    assert conf.active_speaker is None
    second = conf.route_speaker_audio("KN4KCW", b"2", gateway_allowed=True)
    assert second.gateway_allowed is True
    assert conf.active_speaker == "KN4KCW"
