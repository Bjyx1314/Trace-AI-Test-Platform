from app.agents.field_case_generator import append_display_rule_gap_cases, emit_field_cases


def test_emit_field_cases_covers_structured_composition_rule():
    cases = emit_field_cases(
        [
            {
                "block": "资产盘点明细",
                "kind": "list_card",
                "platform": "app",
                "page_path": ["Android AppApp", "资产盘点", "盘点明细"],
                "fields": [{"name": "原因"}],
                "rules": [
                    {
                        "type": "composition",
                        "target": "原因",
                        "condition": "无法验机时",
                        "sources": ["无法验机结构化原因", "补充说明"],
                        "expected": "原因同时包含无法验机结构化原因和补充说明",
                        "raw_text": "无法验机时展示原因，取无法验机结构化原因 + 补充说明拼装",
                    }
                ],
            }
        ],
        platform_keys=["app"],
        default_platforms=["app"],
    )

    composition_cases = [c for c in cases if "原因拼装展示校验" in c["title"]]
    assert len(composition_cases) == 1
    case = composition_cases[0]
    assert case["platforms"] == ["app"]
    assert "display_logic" in case["tags"]
    assert any(str(tag).startswith("display_rule:DR_") for tag in case["tags"])
    assert "原因同时包含无法验机结构化原因和补充说明" in case["expected_result"]
    step = case["steps"][0]
    assert "Android AppApp → 资产盘点 → 盘点明细" in step["action"]
    assert any("不只展示无法验机结构化原因" in cp for cp in step["check_points"])
    assert any("不遗漏补充说明" in cp for cp in step["check_points"])
    assert case["covered_items"][0]["matched_rules"]


def test_emit_field_cases_covers_mapping_and_fallback_rules():
    cases = emit_field_cases(
        [
            {
                "block": "资产盘点列表",
                "kind": "list_columns",
                "platform": "web",
                "page_path": ["web-admin", "资产盘点"],
                "fields": [],
                "rules": [
                    {
                        "type": "mapping",
                        "target": "验机状态",
                        "raw_text": "验机状态按枚举映射为中文文案展示",
                    },
                    {
                        "type": "fallback",
                        "target": "补充说明",
                        "raw_text": "补充说明为空时展示--",
                    },
                ],
            }
        ],
        platform_keys=["web"],
        default_platforms=["web"],
    )

    titles = {c["title"] for c in cases}
    assert "资产盘点列表-验机状态映射展示校验" in titles
    assert "资产盘点列表-补充说明空值兜底展示校验" in titles


def test_append_display_rule_gap_cases_skips_rule_already_covered_by_ai_case():
    template_cases = emit_field_cases(
        [
                {
                    "block": "资产盘点明细",
                    "kind": "list_card",
                    "fields": [],
                "rules": [
                    {
                        "type": "composition",
                        "target": "原因",
                        "condition": "无法验机时",
                        "sources": ["无法验机结构化原因", "补充说明"],
                        "raw_text": "无法验机时原因由无法验机结构化原因+补充说明拼装",
                    }
                ],
            }
        ],
        default_platforms=["app"],
    )
    existing_cases = [
        {
            "title": "无法验机原因拼装展示",
            "expected_result": "无法验机时原因展示需同时包含无法验机结构化原因和补充说明",
            "steps": [],
            "covered_items": [],
        }
    ]

    merged = append_display_rule_gap_cases(existing_cases, template_cases)
    assert merged == existing_cases


def test_legacy_string_rules_still_emit_ordering_case():
    cases = emit_field_cases(
        [
            {
                "block": "资产盘点列表",
                "kind": "list_columns",
                "fields": [],
                "rules": ["按创建时间倒序"],
            }
        ],
        default_platforms=["web"],
    )

    assert any(c["title"] == "资产盘点列表按创建时间倒序展示" for c in cases)
