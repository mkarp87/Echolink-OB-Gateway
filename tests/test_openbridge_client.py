import socket

from echolink_ob.openbridge.client import OpenBridgeClient
from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A, DMRDPacket


def make_packet(dst=310001, slot=1, call_type="group"):
    return DMRDPacket(
        sequence=1,
        rf_source_id=3101234,
        destination_id=dst,
        network_id=31000189,
        slot=slot,
        call_type=call_type,
        frame_type=1,
        dtype_vseq=0,
        stream_id=99,
        payload=CANNED_PAYLOAD_A,
    )


def test_openbridge_client_receives_from_expected_target():
    passphrase = b"TESTPASS"
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.bind(("127.0.0.1", 0))
    target_host, target_port = sender.getsockname()
    client = OpenBridgeClient(
        host=target_host,
        port=target_port,
        passphrase=passphrase,
        network_id=31000189,
        fixed_tgid=310001,
        local_bind_host="127.0.0.1",
        local_bind_port=0,
        timeout=0.1,
    )
    try:
        sender.sendto(make_packet().to_signed(passphrase), client.sock.getsockname())
        got = client.recv_packet()
        assert got is not None
        assert got.rf_source_id == 3101234
        assert client.counters.packets_received == 1
    finally:
        client.close()
        sender.close()


def test_openbridge_client_rejects_wrong_tgid():
    passphrase = b"TESTPASS"
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.bind(("127.0.0.1", 0))
    target_host, target_port = sender.getsockname()
    client = OpenBridgeClient(
        host=target_host,
        port=target_port,
        passphrase=passphrase,
        network_id=31000189,
        fixed_tgid=310001,
        local_bind_host="127.0.0.1",
        local_bind_port=0,
        timeout=0.1,
    )
    try:
        sender.sendto(make_packet(dst=999999).to_signed(passphrase), client.sock.getsockname())
        assert client.recv_packet() is None
        assert client.counters.packets_rejected_tgid == 1
    finally:
        client.close()
        sender.close()
