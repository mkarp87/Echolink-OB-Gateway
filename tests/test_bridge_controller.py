from echolink_ob.audio.pcm import silence_frame
from echolink_ob.bridge.arbiter import GatewayArbiter, GatewayDirection
from echolink_ob.bridge.controller import SimulatedBridgeController
from echolink_ob.echolink.conference import EchoLinkConferenceManager
from echolink_ob.echolink.station import EchoLinkStation
from echolink_ob.identity.callsign import normalize_echolink_callsign
from echolink_ob.vocoder.loopback import LoopbackVocoder


def st(cs):
    return EchoLinkStation(cs, normalize_echolink_callsign(cs))


def test_echolink_to_dmr_constructs_packet_and_repeats_local_audio():
    conf = EchoLinkConferenceManager(max_stations=50)
    for cs in ["K1ABC-L", "N2XYZ", "W3AAA-R"]:
        conf.add_station(st(cs))
    ctl = SimulatedBridgeController(
        conference=conf,
        arbiter=GatewayArbiter(tx_hang_ms=0, max_transmit_seconds=180),
        vocoder=LoopbackVocoder(),
        network_id=31000189,
        fixed_tgid=310001,
    )
    delivery, pkt = ctl.echolink_audio_frame("K1ABC-L", silence_frame(), dmr_source_id=3101234)
    assert sorted(delivery.recipients) == ["N2XYZ", "W3AAA-R"]
    assert pkt is not None
    assert pkt.rf_source_id == 3101234
    assert pkt.destination_id == 310001
    assert pkt.network_id == 31000189


def test_dmr_active_blocks_gateway_but_not_local_echolink_repeat():
    conf = EchoLinkConferenceManager(max_stations=50)
    for cs in ["K1ABC-L", "N2XYZ"]:
        conf.add_station(st(cs))
    arb = GatewayArbiter(tx_hang_ms=0, max_transmit_seconds=180)
    ctl = SimulatedBridgeController(conf, arb, LoopbackVocoder(), 31000189, 310001)
    assert arb.start(GatewayDirection.DMR_TO_ECHOLINK, "3105678")
    delivery, pkt = ctl.echolink_audio_frame("K1ABC-L", silence_frame(), dmr_source_id=3101234)
    assert delivery.recipients == ["N2XYZ"]
    assert delivery.gateway_allowed is False
    assert pkt is None


def test_dmr_audio_broadcast_when_idle():
    conf = EchoLinkConferenceManager(max_stations=50)
    for cs in ["K1ABC-L", "N2XYZ"]:
        conf.add_station(st(cs))
    ctl = SimulatedBridgeController(
        conference=conf,
        arbiter=GatewayArbiter(tx_hang_ms=0, max_transmit_seconds=180),
        vocoder=LoopbackVocoder(),
        network_id=31000189,
        fixed_tgid=310001,
    )
    delivery = ctl.dmr_audio_frame(silence_frame(), source_id=3105678)
    assert delivery is not None
    assert sorted(delivery.recipients) == ["K1ABC-L", "N2XYZ"]
