from app.services.feishu_project import parse_defect


def test_parse_maps_fields():
    raw = {
        "id": "wi-123", "name": "下单超卖",
        "fields": [{"field_key": "root_cause", "field_value": "库存未加分布式锁"},
                   {"field_key": "other", "field_value": "x"}],
    }
    got = parse_defect(raw, "root_cause")
    assert got["external_id"] == "wi-123"
    assert got["title"] == "下单超卖"
    assert got["root_cause"] == "库存未加分布式锁"


def test_parse_missing_rootcause_is_empty():
    raw = {"id": "wi-9", "name": "标题", "fields": []}
    got = parse_defect(raw, "root_cause")
    assert got["root_cause"] == ""
