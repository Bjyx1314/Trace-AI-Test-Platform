"""质量看板聚合纯逻辑单测（架构文档 5.2.3 / 5.3）。

只测不依赖 DB 的 build_summary_cards 与 _evaluate_releasability。
collect_requirement_rows 依赖 DB，由集成测试覆盖。
"""
from app.services import dashboard_metrics as dm


def _row(**kw):
    base = {
        "status": "testing", "total_cases": 0, "passed": 0, "failed": 0,
        "skipped": 0, "p0_open": 0, "p1_open": 0, "p2_open": 0,
        "total_defects": 0, "fixed_defects": 0, "releasability": "not_started",
    }
    base.update(kw)
    return base


# ── 五大指标卡（5.2.3）─────────────────────────────────────────────────────
def test_summary_cards_colors():
    rows = [
        _row(status="done", total_cases=10, passed=10, releasability="pass"),
        _row(status="testing", total_cases=10, passed=7, failed=3,
             p0_open=1, total_defects=2, fixed_defects=1, releasability="block"),
        _row(status="pending_test", total_cases=0),  # 未开始，无用例
    ]
    c = dm.build_summary_cards(rows)

    assert c["requirement_completion"] == {"done": 1, "total": 3, "rate": 33.3, "color": "none"}
    # 测试进度 =（通过+跳过）/ 总用例 = (17+0)/20 = 85% → 橙
    assert c["test_progress"]["rate"] == 85.0
    assert c["test_progress"]["color"] == "orange"
    # 缺陷 2 条且有 P0 未关闭 → 红
    assert c["defect_total"]["color"] == "red"
    # 修复 1/2 = 50% → 橙
    assert c["defect_fix_progress"]["rate"] == 50.0
    assert c["defect_fix_progress"]["color"] == "orange"
    # 已无用例覆盖率卡片
    assert "case_coverage" not in c
    # 阻塞 1 条 → 红
    assert c["blocked_requirements"] == {"count": 1, "color": "red"}


def test_summary_cards_test_progress_counts_skipped():
    # 通过 6 + 跳过 2 + 失败 2，测试进度 =（6+2）/10 = 80% → 橙
    rows = [_row(status="testing", total_cases=10, passed=6, failed=2, skipped=2,
                 releasability="warn")]
    c = dm.build_summary_cards(rows)
    assert c["test_progress"]["rate"] == 80.0
    assert c["test_progress"]["color"] == "orange"


def test_summary_cards_green_path():
    rows = [_row(status="done", total_cases=10, passed=10, releasability="pass") for _ in range(10)]
    c = dm.build_summary_cards(rows)
    assert c["test_progress"]["color"] == "green"   # 100%
    assert c["defect_total"]["color"] == "none"      # 无缺陷
    assert c["blocked_requirements"]["color"] == "none"


def test_summary_cards_empty():
    c = dm.build_summary_cards([])
    assert c["requirement_completion"]["total"] == 0
    assert c["test_progress"]["color"] == "none"
    assert "case_coverage" not in c


def test_defect_total_all_p2_blue():
    rows = [_row(total_cases=5, passed=5, total_defects=3, p2_open=3, releasability="pass")]
    c = dm.build_summary_cards(rows)
    assert c["defect_total"]["color"] == "blue"


# ── 可发布判定（5.3）───────────────────────────────────────────────────────
# 当前规则（简化版）：测试进度 100% 且 P0 无未关闭缺陷 → pass；否则 block；
# total==0 或全部未执行 → not_started。函数签名：(*, total, not_run, pass_rate, p0_open)。
def test_releasability_pass():
    rel, reasons = dm._evaluate_releasability(
        total=10, not_run=0, pass_rate=100.0, p0_open=0)
    assert rel == "pass" and reasons == []


def test_releasability_block_on_open_p0():
    rel, reasons = dm._evaluate_releasability(
        total=10, not_run=0, pass_rate=100.0, p0_open=1)
    assert rel == "block"
    assert any("P0" in r for r in reasons)


def test_releasability_block_on_incomplete_pass_rate():
    # 非 100% 通过 → block（当前无 warn 中间档）
    rel, reasons = dm._evaluate_releasability(
        total=10, not_run=0, pass_rate=85.0, p0_open=0)
    assert rel == "block"
    assert any("100%" in r for r in reasons)


def test_releasability_not_started():
    rel, _ = dm._evaluate_releasability(
        total=0, not_run=0, pass_rate=0.0, p0_open=0)
    assert rel == "not_started"


def test_releasability_not_started_when_all_not_run():
    rel, _ = dm._evaluate_releasability(
        total=5, not_run=5, pass_rate=0.0, p0_open=0)
    assert rel == "not_started"
