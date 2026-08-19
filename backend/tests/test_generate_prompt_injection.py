from app.agents.testcase_generator import _build_generate_prompt


def test_hints_injected_when_present():
    p = _build_generate_prompt("需求A", "内容", None, [], "modA", "platA",
                               "历史教训：必须覆盖 X")
    assert "历史教训：必须覆盖 X" in p
    assert "需求A" in p


def test_no_hints_block_when_absent():
    p = _build_generate_prompt("需求A", "内容", None, [], "modA", "platA", None)
    assert "历史教训" not in p
