from __future__ import annotations

import argparse

from echolink_ob.audio.pcm import generate_sine_pcm, rms, split_frames, join_frames
from echolink_ob.audio.wavdiag import write_wav
from echolink_ob.audio.jitter import JitterBuffer
from echolink_ob.echolink.conference import EchoLinkConferenceManager
from echolink_ob.echolink.station import EchoLinkStation
from echolink_ob.identity.callsign import normalize_echolink_callsign


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline PCM/conference audio self-test")
    parser.add_argument("--output", default="diagnostics/selftest.wav")
    args = parser.parse_args(argv)

    pcm = generate_sine_pcm(seconds=1.0, frequency_hz=1000)
    frames = split_frames(pcm)
    jb = JitterBuffer(max_frames=len(frames))
    for frame in frames:
        jb.push(frame)
    out_frames = [jb.pop() for _ in frames]
    out = join_frames(out_frames)
    write_wav(args.output, out)

    conf = EchoLinkConferenceManager(max_stations=50)
    for cs in ["K1ABC-L", "N2XYZ", "W3AAA-R"]:
        norm = normalize_echolink_callsign(cs)
        conf.add_station(EchoLinkStation(callsign=cs, normalized_callsign=norm))
    delivery = conf.route_speaker_audio("K1ABC-L", frames[0], gateway_allowed=True)

    print(f"wrote {args.output}")
    print(f"frames={len(frames)} rms_in={rms(pcm):.1f} rms_out={rms(out):.1f}")
    print(f"recipients={delivery.recipients}")
    print(conf.roster_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
