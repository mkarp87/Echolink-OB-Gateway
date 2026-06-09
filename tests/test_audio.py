from echolink_ob.audio.jitter import JitterBuffer
from echolink_ob.audio.pcm import BYTES_PER_20MS, generate_sine_pcm, rms, split_frames, silence_frame
from echolink_ob.audio.wavdiag import read_wav, write_wav


def test_pcm_sine_splits_into_20ms_frames():
    pcm = generate_sine_pcm(seconds=1.0)
    frames = split_frames(pcm)
    assert len(frames) == 50
    assert all(len(f) == BYTES_PER_20MS for f in frames)
    assert rms(pcm) > 1000


def test_jitter_buffer_underrun_inserts_silence():
    jb = JitterBuffer(max_frames=2)
    first = silence_frame()
    jb.push(first)
    assert jb.pop() == first
    assert jb.pop() == first
    assert jb.underruns == 1


def test_wav_roundtrip(tmp_path):
    pcm = generate_sine_pcm(seconds=0.1)
    path = tmp_path / "x.wav"
    write_wav(path, pcm)
    back, rate = read_wav(path)
    assert rate == 8000
    assert back == pcm
