from echolink_ob.echolink.gsm import Gsm610Codec, GSM_FRAME_BYTES, GSM_PCM_BYTES, libgsm_available
from echolink_ob.analog.usrp import pcm_sine_frames


def test_gsm_codec_roundtrip_basic():
    assert libgsm_available()
    pcm = bytes(next(pcm_sine_frames(seconds=0.02, frequency_hz=800)))
    with Gsm610Codec.create() as codec:
        encoded = codec.encode_frame(pcm)
        assert len(encoded) == GSM_FRAME_BYTES
        decoded = codec.decode_frame(encoded)
        assert len(decoded) == GSM_PCM_BYTES
        assert decoded != b"\x00" * GSM_PCM_BYTES
