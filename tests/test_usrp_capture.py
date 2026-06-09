from __future__ import annotations

import socket
import threading
import time

from echolink_ob.analog.usrp import UsrpPacket, USRP_VOICE_FRAME_BYTES
from echolink_ob.analog.usrp_capture import capture_usrp_pcm
from echolink_ob.audio.wavdiag import read_wav


def test_capture_usrp_pcm_writes_wav(tmp_path):
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    out = tmp_path / "capture.wav"

    result_box = {}

    def rx():
        result_box["result"] = capture_usrp_pcm(
            host="127.0.0.1",
            port=port,
            seconds=3.0,
            output_wav=out,
            idle_timeout_s=0.5,
        )

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    time.sleep(0.2)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        payload = b"\x34\x12" * 160
        tx.sendto(UsrpPacket(sequence=1, keyup=True, payload=payload).to_bytes(), ("127.0.0.1", port))
        tx.sendto(UsrpPacket(sequence=2, keyup=False).to_bytes(), ("127.0.0.1", port))
    finally:
        tx.close()
    th.join(timeout=3)
    assert "result" in result_box
    report = result_box["result"]
    assert report["voice_packets"] == 1
    assert report["pcm_bytes"] == USRP_VOICE_FRAME_BYTES
    pcm, rate = read_wav(out)
    assert rate == 8000
    assert pcm == payload
