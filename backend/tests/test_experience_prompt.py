from app.services.experience_recall import build_experience_prompt_block


def test_empty_hits_returns_empty_string():
    assert build_experience_prompt_block([]) == ""


def test_block_lists_suggested_items_and_marks_bug():
    hits = [
        {"title": "登录并发", "suggested_covered_items": ["并发登录去重"],
         "hit_reason": "标签命中：登录", "found_bug": True},
        {"title": "金额取整", "suggested_covered_items": ["金额四舍五入"],
         "hit_reason": "语义相近", "found_bug": False},
    ]
    block = build_experience_prompt_block(hits)
    assert "历史教训" in block
    assert "并发登录去重" in block
    assert "金额四舍五入" in block
    assert "曾发现线上/缺陷" in block  # found_bug 标注


def test_respects_limit():
    hits = [{"title": f"t{i}", "suggested_covered_items": [f"item{i}"],
             "hit_reason": "x", "found_bug": False} for i in range(20)]
    block = build_experience_prompt_block(hits, limit=8)
    assert block.count("item") == 8  # 只取前 8 条
