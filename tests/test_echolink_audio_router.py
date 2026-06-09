from echolink_ob.echolink.audio_router import EchoLinkConferenceAudioRouter
from echolink_ob.echolink.conference import EchoLinkConferenceManager
from echolink_ob.echolink.station import EchoLinkStation


def make_router():
    conference = EchoLinkConferenceManager(max_stations=50)
    conference.add_station(EchoLinkStation(callsign="K1ABC-L", normalized_callsign="K1ABC", resolved_dmr_id=3101234))
    conference.add_station(EchoLinkStation(callsign="N2XYZ", normalized_callsign="N2XYZ", resolved_dmr_id=3105678))
    conference.add_station(EchoLinkStation(callsign="W3AAA-R", normalized_callsign="W3AAA"))
    station_frames = []
    gateway_frames = []

    def station_sink(recipient, frame):
        station_frames.append((recipient, frame))

    def gateway_sink(frame, speaker, source_id):
        gateway_frames.append((speaker, source_id, frame))

    router = EchoLinkConferenceAudioRouter(conference, station_sink, gateway_sink)
    return router, station_frames, gateway_frames


def test_speaker_audio_repeats_to_other_stations_and_gateway():
    router, station_frames, gateway_frames = make_router()
    recipients = router.speaker_pcm("K1ABC-L", b"abc", gateway_allowed=True)
    assert recipients == ["N2XYZ", "W3AAA-R"]
    assert [x[0] for x in station_frames] == ["N2XYZ", "W3AAA-R"]
    assert gateway_frames[0][0] == "K1ABC-L"
    assert gateway_frames[0][1] == 3101234
    assert router.snapshot()["active_speaker"] == "K1ABC-L"


def test_dmr_audio_broadcasts_to_all_stations():
    router, station_frames, gateway_frames = make_router()
    recipients = router.dmr_pcm(b"dmr", source_id=1234567)
    assert recipients == ["K1ABC-L", "N2XYZ", "W3AAA-R"]
    assert len(station_frames) == 3
    assert gateway_frames == []
    assert router.snapshot()["current_gateway_source_id"] == 1234567
