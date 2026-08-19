import asyncio

from app.services.runners.android_runner import (
    _append_toast_evidence,
    _enter_text,
    _field_labels_from_failed_checks,
    _is_photo_picker_confirm_text,
    _is_photo_picker_text,
    _is_photo_preview_select_toggle_text,
    _is_photo_thumbnail_selection_text,
    _photo_thumbnail_select_point,
    _photo_picker_context_hint,
    _should_capture_toast,
    _should_trigger_search_after_input,
    _step_needs_photo_picker_context,
    _toast_message_sync,
)


class _FakeDevice:
    def __init__(self):
        self.clicks = []
        self.shells = []
        self.sent = []
        self.text = ""

    def click(self, x, y):
        self.clicks.append((x, y))

    def shell(self, command):
        self.shells.append(command)

    def send_keys(self, text, clear=False):
        self.sent.append((text, clear))
        self.text = text

    def dump_hierarchy(self):
        return f"<node text='{self.text}' />"


class _FakeToast:
    def __init__(self, message=""):
        self.message = message
        self.calls = []

    def get_message(self, timeout=10.0, default=""):
        self.calls.append((timeout, default))
        return self.message or default


class _FakeToastDevice:
    def __init__(self, message=""):
        self.toast = _FakeToast(message)


def test_enter_text_trusts_middle_page_coordinates_for_textarea():
    d = _FakeDevice()

    ok = asyncio.run(_enter_text(d, "设备不在场，无法验机", w=1080, h=2400, x=120, y=1050))

    assert ok is True
    assert d.clicks[0] == (120, 1050)
    assert d.sent == [("设备不在场，无法验机", False)]


def test_form_remark_input_does_not_trigger_search_context():
    assert _should_trigger_search_after_input("填写不验机原因等必填项提交保存", "首台设备处理后任务进入处理中。") is False


def test_search_input_still_triggers_search_context():
    assert _should_trigger_search_after_input("按盘点单号搜索任务", "列表只展示匹配记录") is True


def test_submit_validation_step_captures_toast_evidence():
    act = {"target": "提交验机", "reason": "提交后应看到必填校验提示"}

    assert _should_capture_toast("tap", act, "不上传整机照片或铭牌照片，点击提交 验机必填照片缺失时被拦截") is True


def test_plain_navigation_tap_does_not_wait_for_toast():
    act = {"target": "待办", "reason": "进入待办列表"}

    assert _should_capture_toast("tap", act, "首页 → 待办 → 任务详情") is False


def test_toast_message_is_trimmed_and_appended_to_action_note():
    d = _FakeToastDevice(" 请上传整机照片 ")

    assert _toast_message_sync(d, timeout=0.2) == "请上传整机照片"
    note = asyncio.run(_append_toast_evidence(d, "点击提交验机", timeout=0.2))

    assert note == "点击提交验机；检测到提示「请上传整机照片」"


def test_failed_check_field_labels_are_extracted_for_scroll_probe():
    checks = [
        {"ok": False, "point": "存在整机照片、铭牌照片、其他及不验机原因字段"},
        {"ok": True, "point": "当前为设备处理页"},
    ]

    assert _field_labels_from_failed_checks(checks) == ["整机照片", "铭牌照片", "其他", "不验机原因"]


def test_photo_upload_step_enables_picker_context():
    assert _step_needs_photo_picker_context(
        "在整机照片项目分别尝试上传和拍摄水印照片，并累计添加9张后继续添加第10张",
        "整机照片支持两种来源且上限为9张。",
        ["上传入口和拍摄水印照片入口可用"],
    ) is True


def test_negative_no_upload_step_does_not_enable_picker_context():
    assert _step_needs_photo_picker_context(
        "不选择不验机原因且不上传整机照片或铭牌照片，点击提交",
        "验机必填照片缺失时被拦截。",
        ["整机照片和铭牌照片出现必填校验提示"],
    ) is False


def test_photo_picker_hint_says_thumbnail_content_is_not_current_app():
    hint = _photo_picker_context_hint(True, active=True)

    assert "缩略图里的订单、业务端页面、弹窗、表单" in hint
    assert "不是当前正在操作的业务系统页面" in hint
    assert "回到业务表单且附件缩略图数量增加" in hint


def test_choose_photo_opens_picker_but_is_not_upload_confirmation():
    text = "OCR精准点击「选择照片」"

    assert _is_photo_picker_text(text) is True
    assert _is_photo_picker_confirm_text(text) is False
    assert _is_photo_picker_confirm_text("点击完成，确认上传照片") is True


def test_upload_source_text_is_not_photo_picker_confirmation():
    assert _is_photo_picker_confirm_text("在系统相册中选择一张照片，用于验证上传来源可用") is False


def test_photo_thumbnail_selection_is_detected_and_coordinate_moves_to_checkbox():
    text = "在系统相册中选择一张照片"

    assert _is_photo_thumbnail_selection_text(text) is True
    assert _photo_thumbnail_select_point(136, 370, 1080, 2400) == (222, 274)


def test_preview_select_is_toggle_not_final_upload_confirmation():
    text = "OCR精准点击「选择」 当前为照片选择器预览页，底部“选择”确认按钮可用"

    assert _is_photo_preview_select_toggle_text(text) is True
    assert _is_photo_picker_confirm_text(text) is False
