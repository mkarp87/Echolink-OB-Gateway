import json

from echolink_ob.identity.callsign import normalize_echolink_callsign
from echolink_ob.identity.radioid import RadioIdIndex
from echolink_ob.identity.resolver import IdentityResolver


def test_callsign_normalization_only_known_suffixes():
    assert normalize_echolink_callsign("k1abc-l") == "K1ABC"
    assert normalize_echolink_callsign("K1ABC-R") == "K1ABC"
    assert normalize_echolink_callsign("K1ABC-M") == "K1ABC"
    assert normalize_echolink_callsign("K1ABC-7") == "K1ABC-7"


def test_radioid_json_and_resolver(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"users": [{"callsign": "K1ABC", "id": 3101234}]}))
    idx = RadioIdIndex.from_file(path)
    resolver = IdentityResolver(idx, fallback_source_id=3109999)
    hit = resolver.resolve_echolink("K1ABC-L")
    miss = resolver.resolve_echolink("N0NONE")
    assert hit.normalized_callsign == "K1ABC"
    assert hit.dmr_id == 3101234
    assert not hit.fallback_used
    assert miss.dmr_id == 3109999
    assert miss.fallback_used

from echolink_ob.identity.radioid_update import download_radioid_database, count_radioid_records


def test_radioid_downloader_file_url(tmp_path):
    source = tmp_path / "source-users.json"
    source.write_text(json.dumps({"users": [{"callsign": "K1ABC", "id": 3101234}] * 80}))
    target = tmp_path / "users.json"
    result = download_radioid_database(target, url=source.as_uri(), force=True)
    assert result.ok
    assert result.records == 1
    assert target.exists()
    assert count_radioid_records(target) == 1
    assert (tmp_path / "users.json.meta.json").exists()
