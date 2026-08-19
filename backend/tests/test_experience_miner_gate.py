from app.services.experience_miner import _edit_is_meaningful


def test_reason_present_is_meaningful():
    assert _edit_is_meaningful({"name": "登录", "expected": "成功"},
                               {"name": "登录", "expected": "成功"},
                               "AI 漏了异常分支") is True


def test_name_changed_is_meaningful():
    assert _edit_is_meaningful({"name": "登录成功", "expected": "x"},
                               {"name": "登录失败提示", "expected": "x"}, None) is True


def test_expected_changed_is_meaningful():
    assert _edit_is_meaningful({"name": "登录", "expected": "跳首页"},
                               {"name": "登录", "expected": "跳工作台并提示"}, None) is True


def test_whitespace_only_change_not_meaningful():
    assert _edit_is_meaningful({"name": "登录 ", "expected": "成功"},
                               {"name": "登录", "expected": "成功 "}, None) is False


def test_no_change_not_meaningful():
    assert _edit_is_meaningful({"name": "登录", "expected": "成功"},
                               {"name": "登录", "expected": "成功"}, "  ") is False
