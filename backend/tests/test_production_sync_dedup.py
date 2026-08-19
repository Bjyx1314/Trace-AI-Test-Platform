from app.services.production_sync import filter_new_defects


def test_filters_already_synced():
    parsed = [{"external_id": "a"}, {"external_id": "b"}, {"external_id": "c"}]
    got = filter_new_defects(parsed, {"b"})
    assert [d["external_id"] for d in got] == ["a", "c"]


def test_skips_blank_external_id():
    parsed = [{"external_id": ""}, {"external_id": "a"}]
    got = filter_new_defects(parsed, set())
    assert [d["external_id"] for d in got] == ["a"]
