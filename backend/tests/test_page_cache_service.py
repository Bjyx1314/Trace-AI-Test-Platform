"""页面结构缓存逻辑层单测（架构文档 7.3）。

纯函数测试，不依赖 DB / FastAPI。
"""
from datetime import datetime, timedelta, timezone

from app.services import page_cache_service as p


# ── normalize_url（7.3.2 动态路由 pattern）─────────────────────────────────
def test_normalize_numeric_segment():
    assert p.normalize_url("/admin/users/123?tab=1") == "/admin/users/{id}"


def test_normalize_uuid_segment():
    url = "https://portal.example.test/admin/users/5f47ac10-58cc-4372-a567-0e02b2c3d479/edit"
    assert p.normalize_url(url) == "/admin/users/{uuid}/edit"


def test_normalize_long_hex_segment():
    assert p.normalize_url("/a/deadbeefdeadbeef99/x") == "/a/{hash}/x"


def test_normalize_root_and_empty():
    assert p.normalize_url("/") == "/"
    assert p.normalize_url("") == "/"


# ── compute_region_hashes（7.3.2 区块级 hash）──────────────────────────────
def _regions(order):
    elems = [
        {"name": "a", "selector": "#a", "type": "input"},
        {"name": "b", "selector": "#b", "type": "button"},
    ]
    if order == "rev":
        elems = list(reversed(elems))
    return [{"name": "form", "selector": "#f", "elements": elems}]


def test_region_hash_stable_across_element_order():
    assert p.compute_region_hashes(_regions("fwd")) == p.compute_region_hashes(_regions("rev"))


def test_region_hash_changes_on_selector_change():
    h1 = p.compute_region_hashes(_regions("fwd"))
    mutated = _regions("fwd")
    mutated[0]["elements"][0]["selector"] = "#changed"
    assert p.compute_region_hashes(mutated) != h1


def test_region_hash_skips_unnamed_regions():
    assert p.compute_region_hashes([{"selector": "#x", "elements": []}]) == {}


# ── match_cache（7.3.3 命中决策）───────────────────────────────────────────
def test_match_full_hit():
    h = p.compute_region_hashes(_regions("fwd"))
    assert p.match_cache(h, h)["result"] == "full_hit"


def test_match_partial_hit_on_changed_region():
    h = p.compute_region_hashes(_regions("fwd"))
    changed = dict(h)
    changed["form"] = "X"
    d = p.match_cache(h, changed)
    assert d["result"] == "partial_hit"
    assert d["changed_regions"] == ["form"]
    assert d["miss_regions"] == ["form"]


def test_match_partial_hit_on_new_region():
    h = p.compute_region_hashes(_regions("fwd"))
    extra = dict(h)
    extra["nav"] = "Y"
    d = p.match_cache(h, extra)
    assert d["result"] == "partial_hit"
    assert d["miss_regions"] == ["nav"]
    assert d["changed_regions"] == []  # 新增区块不算 changed


def test_match_no_cache():
    h = p.compute_region_hashes(_regions("fwd"))
    assert p.match_cache(None, h)["result"] == "no_cache"
    assert p.match_cache({}, h)["result"] == "no_cache"


# ── is_stale（7.3.6 失效）──────────────────────────────────────────────────
def test_is_stale_old():
    assert p.is_stale(datetime.now(timezone.utc) - timedelta(days=40)) is True


def test_is_stale_recent():
    assert p.is_stale(datetime.now(timezone.utc) - timedelta(days=5)) is False


def test_is_stale_none():
    assert p.is_stale(None) is False


def test_is_stale_naive_datetime():
    # SQLite 常存 naive UTC，需兼容
    assert p.is_stale(datetime.utcnow() - timedelta(days=40)) is True
