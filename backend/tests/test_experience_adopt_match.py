from app.services.experience_recall import match_adopted_experiences


def test_exact_name_hit():
    hits = [{"experience_id": "e1", "suggested_covered_items": ["并发登录去重"]},
            {"experience_id": "e2", "suggested_covered_items": ["金额四舍五入"]}]
    got = match_adopted_experiences(hits, ["并发登录去重", "其它点"])
    assert got == ["e1"]


def test_substring_hit_counts():
    hits = [{"experience_id": "e1", "suggested_covered_items": ["登录去重"]}]
    got = match_adopted_experiences(hits, ["并发登录去重校验"])
    assert got == ["e1"]


def test_no_hit_returns_empty():
    hits = [{"experience_id": "e1", "suggested_covered_items": ["A"]}]
    assert match_adopted_experiences(hits, ["B", "C"]) == []
