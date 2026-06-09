from echolink_ob.echolink.access import EchoLinkAccessRules, detect_station_type


def test_detect_station_type_from_suffix():
    assert detect_station_type("K1ABC-L") == "link"
    assert detect_station_type("K1ABC-R") == "repeater"
    assert detect_station_type("K1ABC") == "user"


def test_access_blocks_conferences_by_default():
    rules = EchoLinkAccessRules(allow_patterns=["*"], allow_conferences=False)
    decision = rules.check("*CONF*")
    assert not decision.allowed
    assert decision.reason == "station_type_blocked:conference"


def test_access_allow_and_deny_patterns():
    rules = EchoLinkAccessRules(allow_patterns=["K1*"], deny_patterns=["K1BAD"])
    assert rules.check("K1ABC").allowed
    assert not rules.check("N2XYZ").allowed
    assert rules.check("N2XYZ").reason == "not_allowlisted"
    assert not rules.check("K1BAD").allowed
    assert rules.check("K1BAD").reason == "denied"
