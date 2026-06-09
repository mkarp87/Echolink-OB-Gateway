from echolink_ob.echolink.rtp import build_gsm_rtp, parse_rtp, build_rtcp_sdes, parse_rtcp_sdes, build_rtcp_bye, is_rtcp_bye, RTP_PAYLOAD_GSM, build_ndata_info, parse_echolink_text_packet, parse_station_identity_text, RTP_VERSION_LEGACY_MARKER, is_disconnect_text


def test_rtp_build_parse_gsm():
    payload = b"x" * 132
    data = build_gsm_rtp(payload, sequence=123, timestamp=456, ssrc=789)
    pkt = parse_rtp(data)
    assert pkt.sequence == 123
    assert pkt.timestamp == 456
    assert pkt.ssrc == 789
    assert pkt.payload_type == RTP_PAYLOAD_GSM
    assert pkt.payload == payload


def test_rtp_parse_accepts_mobile_marker_bit():
    payload = b"x" * 33
    data = build_gsm_rtp(payload, sequence=1, timestamp=160, ssrc=2, marker=True)
    pkt = parse_rtp(data)
    assert pkt.payload_type == RTP_PAYLOAD_GSM
    assert pkt.marker is True
    assert pkt.payload == payload


def test_rtp_parse_accepts_legacy_first_byte():
    payload = b"y" * 33
    data = build_gsm_rtp(payload, sequence=2, timestamp=320, ssrc=3, first_byte=RTP_VERSION_LEGACY_MARKER)
    pkt = parse_rtp(data)
    assert pkt.payload_type == RTP_PAYLOAD_GSM
    assert pkt.first_byte == RTP_VERSION_LEGACY_MARKER


def test_rtcp_sdes_and_bye():
    data = build_rtcp_sdes(callsign="K1ABC-L", name="Tester")
    parsed = parse_rtcp_sdes(data)
    assert parsed["callsign"] == "K1ABC-L"
    assert "K1ABC-L" in parsed["name"]
    assert is_rtcp_bye(build_rtcp_bye())


def test_parse_echolink_station_identity_ndata():
    data = build_ndata_info("Station KN4KCW\n\nMichael\n\nGreenville NC\n\niPhone")
    text = parse_echolink_text_packet(data)
    ident = parse_station_identity_text(text)
    assert ident["callsign"] == "KN4KCW"
    assert ident["name"] == "Michael"
    assert ident["location"] == "Greenville NC"
    assert ident["client"] == "iPhone"


def test_parse_plain_station_identity_packet():
    data = b"Station KN4KCW\r\rMichael\r\rGreenville NC\r\riPhone\x00"
    text = parse_echolink_text_packet(data)
    ident = parse_station_identity_text(text)
    assert ident["callsign"] == "KN4KCW"


def test_echolink_goodbye_text_is_disconnect_command():
    assert is_disconnect_text(parse_echolink_text_packet(build_ndata_info("Goodbye")))
    assert is_disconnect_text("BYE")
    assert is_disconnect_text("Disconnecting")
    assert not is_disconnect_text("Connected stations:\nGOODBYE-L DMR=123")
