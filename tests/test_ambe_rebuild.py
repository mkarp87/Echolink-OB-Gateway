from echolink_ob.dmr.ambe import (
    build_payload33_from_ambe72_triplet,
    extract_ambe72_from_payload33,
    extract_center48_from_payload33,
)
from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A


def test_rebuild_payload_preserves_ambe_voice_regions():
    frames = extract_ambe72_from_payload33(CANNED_PAYLOAD_A)
    center = extract_center48_from_payload33(CANNED_PAYLOAD_A)
    rebuilt = build_payload33_from_ambe72_triplet(frames, center)
    assert rebuilt == CANNED_PAYLOAD_A
