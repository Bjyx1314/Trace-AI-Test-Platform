"""AndroidAgentRunner —— AI 视觉直连真机执行 App 手动用例(无 Appium)。

逐步判定(rigorous)：对用例的【每个步骤】单独操作并判定 pass/fail/blocked：
- uiautomator2 直连真机，每步循环：截图→AI(gpt-5.x 视觉)决定下一个操作或给出本步结论；
- 关键纪律：没有实际可核对的数据/内容时(如列表"暂无数据")必须判 blocked(无法验证)，禁止臆断为 pass；
- 每个步骤的截图归到该步骤；整条用例仅当所有步骤 pass 才"通过"，否则"失败"并写明哪步、原因。
Flutter/webview 应用拿不到原生控件树，故全程走"看截图点坐标"。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from .base import BaseRunner, RunOutcome, RunContext
from app.services import execution_control
from app.services.runners import app_ocr
from app.services.runners import upload_fixtures

logger = logging.getLogger(__name__)

# 同一真机同一时刻只能跑一条 App 用例(一块屏)，用进程内锁串行化，多入口/并发触发时自动排队
_DEVICE_LOCK = asyncio.Lock()

# App 自动登录执行态记录：同一执行(execution)同一设备(serial)记住「已成功登过」。
# 但每条用例前我们会冷启动 App，部分应用会掉回登录页，所以后续仍要做一次 run_login 轻量校验；
# run_login 若已在首页会秒退，不会真正重复输入账号。
_LOGGED_IN: set = set()

_u2_patched = False


def _patch_u2_tolerant_check_alive() -> None:
    """uiautomator2 首次连一台从未起过 uiautomator2 server 的设备时，其内部探活(_check_alive)
    只兜底 HTTPError/ConnectionError——设备侧服务未启动很正常，本该被判"不在线"后自动推送/启动。
    但部分中转链路(如 Sonic 的网络adb转发)在服务未就绪时不会干净地拒绝连接，而是把协议层错误
    (如 respCheck: closed)当数据回传，触发未被兜底的 http.client.BadStatusLine，导致库在走到
    "自动安装启动"这一步前就直接抛异常。这里把该异常也纳入"判定不在线"，让库正常走自动启动流程。
    """
    global _u2_patched
    if _u2_patched:
        return
    try:
        import uiautomator2.core as u2core
        _orig_check_alive = u2core.BasicUiautomatorServer._check_alive

        def _tolerant_check_alive(self):
            try:
                return _orig_check_alive(self)
            except Exception:
                return False

        u2core.BasicUiautomatorServer._check_alive = _tolerant_check_alive
        _u2_patched = True
    except Exception:
        logger.warning("patch uiautomator2._check_alive 失败，跳过(不影响正常设备)", exc_info=True)

_UPLOADS = Path(__file__).resolve().parents[3] / "uploads" / "exec_shots"
_SEND_W = 540                # 发给 AI 的截图宽度(等比缩放)，AI 坐标需按 scale 还原回设备分辨率
_MAX_ACTIONS_PER_STEP = 20   # 单步操作上限(防 AI 无限循环/烧钱的兜底)。导航靠 OCR 滚动直达(不占 AI 动作)，20 步够做搜索/判定
_STUCK_LIMIT = 3             # 连续 N 张截图完全相同视为"卡住无进展"，提前结束本步
# 动作循环给 AI 的输出 token 上限。gpt-5.x 等推理模型的 max_output_tokens 含"思考"token，
# 给太小(如 600)会被思考吃光、真正的动作 JSON 被截断/为空 → 执行时"未取到有效动作"卡住。给足余量。
_ACTION_MAX_TOKENS = 4000
# AI reason 里出现这些词，说明它认为当前有系统原生弹窗遮挡 → 才触发确定性关弹窗(一次 dump)，
# 平时不做任何弹窗探测，零开销。
_NATIVE_POPUP_HINTS = ("弹窗", "权限", "允许", "照片", "媒体", "文件", "授权", "通知", "引导", "更新弹")
_TOAST_EVIDENCE_HINTS = (
    "必填", "校验", "提示", "toast", "拦截", "不能为空", "不允许", "超过", "限制", "缺失",
    "失败", "异常", "成功", "提交", "保存", "确认", "确定", "完成", "上传",
)
_TOAST_TRIGGER_ACTIONS = ("tap", "enter", "paste")
_PHOTO_UPLOAD_HINTS = ("上传", "拍摄", "选择照片", "添加", "照片", "图片", "附件", "水印", "相册")
_PHOTO_PICKER_HINTS = ("选择照片", "系统相册", "相册", "图库", "照片缩略图", "图片缩略图", "照片选择", "图片选择")
_PHOTO_PICKER_CONFIRM_HINTS = ("完成", "确定", "确认", "添加", "使用", "上传", "保存")
_PHOTO_THUMBNAIL_SELECT_HINTS = ("缩略图", "选择一张照片", "选中一张照片", "选择一张图片", "选中一张图片", "系统相册中选择")


def _now_hint() -> str:
    """把【当前真实时间】告诉 AI。

    截图状态栏通常只有时钟没有日期，AI 无从判断"这条记录是不是刚刚创建的"——实测它把三天前的
    2026-07-14 10:41 当成"刚刚"，于是把一张旧单认成自己刚建的成果(单号 IN20260714… 一直复用)。
    给出当前时间后，"创建时间=刚刚"才是可验证的。
    """
    from datetime import datetime
    return (f"【当前真实时间】{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            "（判断“某条记录是不是刚刚创建的”一律以此为准：只有创建时间与它相差几分钟内才算“刚刚”；"
            "几小时前/昨天/前几天的记录，绝不是本次刚创建的）\n\n")


def _prior_steps_hint(ui_trace: list[dict]) -> str:
    """把【已完成步骤的结论】带给后续步骤。

    否则每步都是"失忆"的：步1 新建拿到的单号，步2 完全不知道，就会自作主张去列表里另找一条
    "看起来更相关"的旧记录来核对(实测 0240：步1 建了 IN…866634，步2 却点进旧的 IN…071305)。
    """
    if not ui_trace:
        return ""
    lines = []
    for st in ui_trace[-3:]:          # 最近 3 步足够，避免提示词过长
        r = (st.get("reason") or "").strip().replace("\n", " ")
        lines.append(f"- 步骤{st.get('seq')}[{st.get('verdict_cn') or st.get('verdict')}]：{r[:180]}")
    return ("【前序步骤结论】(本用例前面几步的真实结果，承接它继续做；其中出现的单号/编号/资源编码等"
            "【就是本用例自己刚产生的数据】，后续核对【必须针对它】，不要另找列表里别的旧记录)：\n"
            + "\n".join(lines) + "\n\n")


def _step_text_targets(action: str, expected: str = "") -> list[str]:
    text = " ".join(s for s in [action, expected] if s)
    out: list[str] = []
    for m in re.findall(r"[“\"']([^”\"']{1,8})[”\"']", text):
        if any("\u4e00" <= ch <= "\u9fff" for ch in m):
            out.append(m.strip())
    for kw in ("报修", "报停", "索赔", "待办", "工作台", "现场处理", "预约到场时间"):
        if kw in text:
            out.append(kw)
    seen: set[str] = set()
    return [x for x in out if x and not (x in seen or seen.add(x))]


def _primary_tap_target(targets: list[str]) -> str | None:
    if not targets:
        return None
    for text in targets:
        if text not in ("待办", "工作台"):
            return text
    return targets[0]


def _should_capture_toast(action: str, act: dict, step_text: str) -> bool:
    """Only wait for toast evidence when the step/action can reasonably emit transient feedback."""
    a = (action or "").lower()
    if a not in _TOAST_TRIGGER_ACTIONS:
        return False
    text = " ".join([
        step_text or "",
        str(act.get("target") or ""),
        str(act.get("reason") or ""),
        str(act.get("text") or ""),
    ])
    return any(hint in text for hint in _TOAST_EVIDENCE_HINTS)


def _toast_reset_sync(d) -> None:
    try:
        d.toast.reset()
    except Exception:
        pass


def _toast_message_sync(d, timeout: float = 1.0) -> str:
    try:
        msg = d.toast.get_message(timeout=timeout, default="")
    except TypeError:
        try:
            msg = d.toast.get_message(timeout=timeout)
        except Exception:
            return ""
    except Exception:
        return ""
    return str(msg or "").strip()


async def _toast_reset(d) -> None:
    await asyncio.to_thread(_toast_reset_sync, d)


async def _append_toast_evidence(d, desc: str, timeout: float = 1.0) -> str:
    msg = await asyncio.to_thread(_toast_message_sync, d, timeout)
    if not msg:
        return desc
    return f"{desc}；检测到提示「{msg[:80]}」"


def _clean_check_label(label: str) -> str:
    label = re.sub(r"\[[^\]]+\]", "", str(label or ""))
    label = re.sub(r"[：:（）()【】\s]", "", label)
    label = re.sub(r"(全部|所有|对应|当前|页面|字段|控件|入口|按钮|信息|列表|卡片|展示|显示|存在)$", "", label)
    label = label.strip("，,。、；;及和与的")
    if not (1 <= len(label) <= 12):
        return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in label):
        return ""
    return label


def _field_labels_from_failed_checks(checks_result: list[dict]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for check in checks_result:
        if check.get("ok"):
            continue
        point = str(check.get("point") or "")
        candidates: list[str] = []
        candidates.extend(re.findall(r"[“\"']([^”\"']{1,12})[”\"']", point))
        for m in re.finditer(r"(?:存在|展示|显示|包含|看到|可见|出现)([^。；;]*?)(?:字段|控件|入口|按钮|列|项|模块|信息)", point):
            candidates.extend(re.split(r"[、,，/]|以及|及|和|与", m.group(1)))
        for raw in candidates:
            label = _clean_check_label(raw)
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


async def _scroll_to_missing_check_label(d, labels: list[str], w: int, h: int, max_swipes: int = 6) -> str | None:
    if not labels or not app_ocr.available():
        return None
    pending = list(labels)
    try:
        img = await asyncio.to_thread(d.screenshot)
    except Exception:
        img = None
    if img is not None:
        still_pending: list[str] = []
        for label in pending:
            if not await asyncio.to_thread(app_ocr.locate_text, img, label):
                still_pending.append(label)
        pending = still_pending
    if not pending:
        return f"AI判定缺失字段前，系统OCR已在当前屏看到检查字段：{'、'.join(labels[:6])}"
    for idx in range(max_swipes):
        try:
            await asyncio.to_thread(_swipe, d, w, h, "up")
            await asyncio.sleep(0.7)
            img = await asyncio.to_thread(d.screenshot)
        except Exception:
            return None
        for label in pending:
            if await asyncio.to_thread(app_ocr.locate_text, img, label):
                return f"AI判定缺失字段前，系统继续下滑核对，在第{idx + 1}次下滑后找到「{label}」"
    return None


def _step_needs_photo_picker_context(action: str, expected: str = "", checks: list | None = None) -> bool:
    text = " ".join([str(action or ""), str(expected or "")] + [str(c) for c in (checks or [])])
    if "不上传" in text:
        return False
    has_photo = any(h in text for h in ("照片", "图片", "附件", "水印", "相册"))
    has_upload = any(h in text for h in ("上传", "拍摄", "选择照片", "添加", "累计添加"))
    return has_photo and has_upload and any(h in text for h in _PHOTO_UPLOAD_HINTS)


def _photo_picker_context_hint(enabled: bool, active: bool = False) -> str:
    if not enabled:
        return ""
    state = "当前可能已在相册/照片选择器中。" if active else "本步骤可能会进入相册/照片选择器。"
    return (
        "【照片上传上下文】"
        f"{state} 如果截图中看到相册/图库/照片网格/缩略图，缩略图里的订单、业务端页面、弹窗、表单等"
        "都只是“待上传照片的内容”，不是当前正在操作的业务系统页面；不能因为缩略图内容看起来像别的系统就返回、关闭或改走业务导航。"
        "在照片网格里要点小缩略图右上角的勾选圆点/选择框，不要点图片主体进入大图预览。"
        "如果已经进入大图预览，底部“选择”通常只是勾选开关，不是最终完成；勾选后要返回照片网格，再点击完成/确定/添加/使用/上传等确认按钮。"
        "只有回到业务表单且附件缩略图数量增加，才算上传成功。\n\n"
    )


def _is_photo_picker_text(text: str) -> bool:
    return any(hint in str(text or "") for hint in _PHOTO_PICKER_HINTS)


def _is_photo_picker_confirm_text(text: str) -> bool:
    text = str(text or "")
    if _is_photo_preview_select_toggle_text(text):
        return False
    if any(hint in text for hint in ("完成", "确定", "确认", "添加", "使用", "保存")):
        return True
    return any(hint in text for hint in ("「上传」", "“上传”", "上传按钮", "点击上传"))


def _is_photo_thumbnail_selection_text(text: str) -> bool:
    text = str(text or "")
    if "选择照片" in text or _is_photo_picker_confirm_text(text):
        return False
    return any(hint in text for hint in _PHOTO_THUMBNAIL_SELECT_HINTS)


def _is_photo_preview_select_toggle_text(text: str) -> bool:
    text = str(text or "")
    return "选择照片" not in text and ("「选择」" in text or "“选择”" in text or "底部“选择”" in text)


_DEVICE_PHOTO_DIR = "/sdcard/DCIM/Camera"


async def _seed_device_photos(d, count: int, size_kb: int) -> int:
    """把 count 张约 size_kb 的测试照片推进设备相册并刷新媒体库，返回成功推送的张数。

    App 端的上传【必须】经过系统相册，而相册里有没有"符合本用例要求(张数够、单张够大/够小)"
    的照片，是执行端此前完全没管的前提——真机相册是什么就是什么，用例要 30 张而相册只有 3 张，
    AI 在选择器里怎么点都凑不出来。这里把这个前提变成确定性的。

    媒体库刷新在各 Android 版本上行为不一致(10+ 收紧了 MEDIA_SCANNER 广播)，所以广播 + 逐文件
    扫描都试一遍；失败不抛错，返回实际推成功的张数交调用方据实反馈。
    """
    from pathlib import Path

    paths = upload_fixtures.make_images(count, size_kb)

    def _push_all() -> int:
        ok = 0
        try:
            d.shell(["mkdir", "-p", _DEVICE_PHOTO_DIR])
        except Exception:  # noqa: BLE001 目录多半已存在
            pass
        for p in paths:
            remote = f"{_DEVICE_PHOTO_DIR}/{Path(p).name}"
            try:
                d.push(p, remote)
                ok += 1
            except Exception as e:  # noqa: BLE001 单张失败不影响其余
                logger.warning("推送测试照片失败 %s：%s", remote, e)
                continue
            for scan in (
                f'am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{remote}',
                f'content call --uri content://media/external/images/media --method scan_file --arg {remote}',
            ):
                try:
                    d.shell(scan)
                    break
                except Exception:  # noqa: BLE001 换下一种扫描方式
                    continue
        return ok

    try:
        return await asyncio.to_thread(_push_all)
    except Exception as e:  # noqa: BLE001
        logger.warning("注入测试照片整体失败：%s", e)
        return 0


def _photo_thumbnail_select_point(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    """Move from thumbnail body toward its upper-right selection checkbox."""
    nx = min(max(5, x + int(w * 0.08)), w - 5)
    ny = min(max(5, y - int(h * 0.04)), h - 5)
    return nx, ny


# 版本更新弹窗：出现即关掉，别让 AI 靠视觉反复点、耗步数。仅在检测到更新文案时才关，
# 避免误关正常业务弹窗。关法：点弹窗【右上角的关闭(×)图标】，不点"以后再说/暂不更新"这类按钮
# (部分 App 这些按钮不存在或行为不对；× 关闭最通用)。× 是图标、无文字，故走 content-desc + 视觉定位。
_UPDATE_MARKERS = ("发现新版本", "检测到新版本", "版本更新", "有新版本", "立即更新", "更新提示", "升级提示", "版本升级")
_UPDATE_CLOSE_DESC = ("关闭", "关闭弹窗", "close", "Close", "取消", "×", "✕", "x", "X")

# 检测到不在目标 App 时会 monkey 重拉，但这些是系统 UI/弹窗，不该被"重拉"打断(权限/安装框由专门逻辑处理)
_SKIP_RELAUNCH = ("systemui", "inputmethod", "packageinstaller", "com.android.permission",
                  "com.google.android.permission", "com.android.settings")


async def _dismiss_update_popup(d, provider=None, b64=None, scale_x=1.0, scale_y=1.0) -> str | None:
    """检测到"发现新版本"类更新弹窗 → 点其【右上角关闭(×)图标】关掉(不点"以后再说")。返回说明，否则 None。"""
    try:
        has_update = any((await asyncio.to_thread(lambda m=m: d(textContains=m).exists)) for m in _UPDATE_MARKERS)
    except Exception:
        return None
    if not has_update:
        return None
    # ① 原生找关闭图标(content-desc，图标常带无障碍描述)
    for desc in _UPDATE_CLOSE_DESC:
        try:
            el = d(description=desc)
            if await asyncio.to_thread(lambda e=el: e.exists):
                await asyncio.to_thread(el.click)
                return f"关闭图标(desc={desc})"
        except Exception:
            continue
    # ② 视觉定位弹窗右上角的 × 并点(× 无文字，原生取不到时靠视觉)
    if provider is not None and b64 is not None:
        try:
            from app.agents.llm import _extract_json
            raw = await provider.text_multi(
                _TARGET_TAP_SYSTEM,
                "目标文案：『发现新版本/版本更新』弹窗【右上角】的关闭按钮(× / ✕ 图标)\n请在截图中定位它的中心坐标；找不到就 not_found。",
                [(b64, "image/jpeg")], 500, reasoning_effort=_reasoning_effort(),
            )
            a = _extract_json(raw)
            if (a.get("action") or "").lower() == "tap":
                dx, dy = int(a.get("x", 0) * scale_x), int(a.get("y", 0) * scale_y)
                await asyncio.to_thread(d.click, dx, dy)
                return f"关闭×({dx},{dy})"
        except Exception:
            pass
    return None


def _reasoning_effort() -> str | None:
    """视觉动作循环用低推理档：省 token 且对"看图输出点击"更稳。留空则不下发该参数。"""
    try:
        from app.config import settings
        return (settings.vision_reasoning_effort or "").strip() or None
    except Exception:
        return "low"

_SYSTEM = (
    "你是认真细致的手机 App 自动化测试代理，当前只专注【一个测试步骤】。会给你：该步骤的操作与预期、本步已执行操作、当前手机截图。"
    "像测试员一样，先把界面导航/操作到位、充分核对后，再判定本步骤。严格输出一个 JSON(不要解释/markdown)：\n"
    '{"action":"tap|input|enter|paste|long_press|double_tap|drag|clear|swipe|back|wait|upload|date|select|judge",'
    '"x":数,"y":数,"x2":数,"y2":数,"text":"输入内容","text2":"日期区间的结束值",'
    '"options":["下拉要选的选项文案"],'
    '"target":"tap时要点的控件确切文案","count":上传张数,"size_mb":单张大小MB,'
    '"direction":"up|down|left|right","verdict":"pass|fail|blocked","reason":"依据或原因",'
    '"checks":[{"point":"锚点原文","ok":true或false}]}\n'
    "动作说明：tap点击/input输入/enter回车(触发搜索)/paste粘贴剪贴板/long_press长按(x,y, 弹上下文菜单/复制/删除)/"
    "double_tap双击(x,y)/drag拖拽(从 x,y 拖到 x2,y2, 用于滑块/拖动排序)/clear清空当前输入框/"
    "swipe滑动/back返回/wait等待/upload上传文件/date填日期(区间)/select下拉选项/judge判定。\n"
    "规则：\n"
    "- 坐标 x,y 用当前截图像素坐标(左上角0,0)指控件中心；tap点击/input输入/enter回车(触发搜索)/paste粘贴/swipe滑动/back返回/wait等待；遇无关弹窗(如版本更新)先关闭；\n"
    "- 【tap 必须写 target】做 tap 时除了 x,y，还要用 target 写清你【这一下要点的那个控件的确切文案】(如“新建”“提交”“确定”)——"
    "系统会用 target 精准定位点击。【别把 target 写成当前所在页面/列表的名字】：比如你在“业务单据”列表页、想点右上角“新建”，target 要写“新建”而不是“业务单据”，否则会点错、原地打转。\n"
    "- 【PC 网页选下拉(尤其多选)用 action=select，别一项一项点】要在下拉里选值时，直接输出 "
    '{"action":"select","x":下拉框中心x,"y":下拉框中心y,"options":["选项A","选项B"]}'
    "——系统会展开该下拉、按【文案】把这些选项一次性选完，并回读控件上实际显示的已选项。"
    "多选下拉尤其要用它：一项一项点意味着展开、点选项、再展开…几个下拉就能把单步动作预算耗光，"
    "用例还没走到搜索就判无法验证。options 里写页面上【看到的选项原文】；某项确实不存在时，"
    "系统会明确告诉你「未找到」，据此判定即可，不要反复重试；\n"
    "- 【PC 网页填日期/日期区间用 action=date，别去点日历格子】要填「创建时间」「完成时间」这类"
    '日期或日期区间时，直接输出 {"action":"date","x":日期框中心x,"y":日期框中心y,'
    '"text":"2026-08-01","text2":"2026-08-31"}(单个日期就省略 text2)——系统会把日期【直接键入】控件。'
    "【区间选择器最容易翻车】：它要求【开始和结束都选定】，在那之前底部的『确定』是【禁用】的，"
    "你只选了开始就去点确定，会连点几次毫无反应、白白耗光步数。用 date 一次把两端都填了，"
    "也不用为了跨月去反复点上/下月箭头；\n"
    "- 【同一个位置连点两次没反应就换招】仅指【点击】：若提示明确告诉你上一次【点击】后界面没有任何变化，"
    "多半点到了【禁用】按钮或点空了，别再点同一个坐标——先补齐该控件要求的前置选择(如区间日期的另一端、"
    "必填项)，或改用 date/upload 这类确定性动作。"
    "【滚动不适用本条】：列表滚到底、宽表横向滚到头时画面本来就不再变化，那是正常的，"
    "此时该【停下来判定】，不要反复来回滚；\n"
    "- 【要上传图片/附件就用 action=upload，绝不要自己去点上传控件碰运气】看到步骤要求"
    "『上传N张图片/传附件/选择照片』时，直接输出 "
    '{"action":"upload","x":上传控件中心x,"y":上传控件中心y,"count":张数,"size_mb":单张大小}'
    "——系统会【自己造出测试图片并直接喂给页面/相册】，不需要你去找文件、也不会弹出你看不见的文件对话框。"
    "count/size_mb 按步骤文案填(如“上传30张”→count=30；“大于10M”→size_mb=10.1；没写大小就省略)。"
    "【最常见的失败就是不用 upload 而反复 tap 上传控件】：那会弹出系统文件框，截图里什么都看不到、"
    "页面也点不动，连点几次就把步数耗光、白白判成无法验证。上传后要 tap 一次页面空白处或直接看截图，"
    "确认附件缩略图数量变了再判定；\n"
    "- 【验证“复制”功能用 paste】要核对某字段的『复制』按钮/图标是否好用时：先 tap 该复制图标(把值复制到剪贴板)，"
    "再对一个输入框(如搜索框)输出 action=paste【并在 x,y 给出该输入框坐标】把剪贴板粘进去，然后核对粘进去的值是否等于被复制的字段值。"
    "不要自己手打那个值来“假装”验证复制；\n"
    "- 【输入就一步到位】要往搜索框/输入框打字时，直接输出 input 并【在 x,y 里给出该输入框的坐标】——"
    "系统会自动先点它聚焦再输入，你【不要】用单独的 tap 去反复聚焦(那样常点空、白白耗步数)。切换搜索字段(下拉选字段)后要搜索，"
    "就直接对搜索框发 input(带搜索框坐标)。若上一步已 input 但截图里搜索框仍为空/没有你输入的值，多半是没聚焦到框——"
    "换搜索框正文区域的坐标重新 input 一次，别再空点；\n"
    "- 【搜索触发方式要自己探索，但别盲点】输入搜索关键字后，列表【不一定会实时过滤】——不同 App 触发方式不同(实时/回车/搜索按钮)。"
    "若 input 后列表条数没变(没过滤)，先【触发搜索】，顺序：\n"
    "  ①【首选 action=enter】回车触发——不需要在屏幕上找任何按钮，最稳，优先用它；\n"
    "  ②只有当你在【当前截图里确实清楚看到】键盘右下角的“搜索/Go/完成”键、或页面上的搜索按钮/放大镜图标时，才 tap 它(给准它的坐标)；\n"
    "  ③【识别不到就不要点】看不清/找不到这些按钮时，【绝不要凭猜反复点某个位置】去触发——那只会白白耗步数；此时就只靠 enter。\n"
    "  触发过 enter(必要时加一次看得清的搜索键)后若仍不过滤，就据此判定，【不要为了触发搜索一直点、死循环】；\n"
    "- 【滚动要滚对列/区域】swipe 不带坐标时默认在屏幕正中滚动；当界面是【多列选择器/并排列表】(如时间选择器：左『日期』列、右『时间』列)或要滚动某个特定区域时，"
    "【必须在 swipe 里带上 x,y】=你要滚动的那一列/区域的中心像素，否则会滚到相邻列、导致目标列纹丝不动、空滚到步数耗尽。"
    "例如要把左侧日期列滚到更早的历史日期：{\"action\":\"swipe\",\"direction\":\"down\",\"x\":<日期列中心x>,\"y\":<日期列中部y>}(down=手指下拉，把上方更早的日期拉出来)；"
    "若滚了两三次目标列没有任何变化，多半是 x 落在了别的列上，换成目标列的 x 再滚；\n"
    "- 【精确导航·子系统要分清】本步开始时界面可能停留在上一步/首页，先核对当前是否为本步骤的正确页面，不是则【坚持导航】过去(可多次点菜单/展开子菜单/返回换路径，不要点一次进不去就放弃)；"
    "严格按步骤描述进入正确的子系统与入口，特别注意区分名称相近但不同的入口——例如“金融工作台”与“物流工作台”是不同子系统、“新建任务”与“新建任务计划”是不同入口，必须进入与描述完全一致的那个；"
    "若发现进错了子系统/页面(如要金融工作台却进了物流工作台)，用 back 返回重新找正确入口，绝不将就用错的页面去判定；\n"
    "- 【只认路径里的确切名字，不要猜相近入口】步骤给了导航路径(如“工作台→资源管理→资源列表”)时，只点【与路径节点名完全一致】的入口。"
    "【找入口靠“逐屏扫描、见到即点”，别跳读】进入某页后【先看当前这一屏(尤其上半屏/服务中心宫格区)有没有目标名称，"
    "有(如“资产盘点”常在顶部宫格)就【立刻点】】；当前屏没有，就【一屏一屏地往下滚，每滚一屏都先在新屏里找、找到就点、点了就停】。"
    "两个方向都要照顾：顶部的入口【别一进去就往下滑、把它滑过去】；底部的入口(如“资源列表”常在页面最下面)【就一直逐屏滚到底去找，滚到底也正常】。"
    "唯一要避免的是【一次性甩到最底、跳过中间和顶部漏看】——那样顶部的会被越过、也可能滑过头。逐屏找到即停，既不过早放弃、也不越过目标；"
    "【绝不】因为没看到就退而进入一个【名字不同的相近入口】去猜路(如目标是“资源列表”却去点“资源/我的资源/电瓶/仓库领取”)——那些是不同功能，进去只会越走越偏、还常是空数据。"
    "确实把当前页滚到底都找不到确切名称的入口，才 back 换一条路或判 blocked，切勿靠猜；\n"
    "- 【复合步骤·子动作按序做全，不跳中间步】当一个步骤用逗号/顿号/箭头串了多个子动作(如“…点击处理报修，处理方式选择现场处理，点击预约到场时间”)，"
    "必须【严格按先后顺序逐个完成】，尤其不能跳过中间的单选/开关/模式切换/勾选(如选中『现场处理』这类 radio)。"
    "关键提示：若步骤描述里的最终目标控件(如“预约到场时间”)在当前屏找不到，通常是因为它【前面的某个子动作(如切换处理方式为现场处理)还没做】——"
    "此时应回到未完成的那个子动作先把它做掉(该字段/入口往往就会出现)，而不是在当前模式下硬找、或拿相近的别的字段(如“沟通时间”)将就；\n"
    "- 【某个子项做不了，也要把其余能做的都做完、都验证到，别整步提前放弃】当一步里有多个【可各自独立操作】的子项(如“任务状态、盘点类型、服务网点、服务工程师分别多选筛选”这类并列筛选/勾选)，"
    "若其中某个子项确实因【控件缺失/页面上根本没有/不可用】而做不了，【不要因此就整步收场、连能做的都不做】——要【跳过做不了的那个，继续把其余存在的子项逐个做完并验证】(如服务网点/服务工程师控件不存在，仍要把任务状态、盘点类型、创建时间该选的都选上、看筛选是否生效)；"
    "最后在 reason 里【逐项说清：哪些子项已完成并生效、哪个子项因缺控件未能完成】，再据此判 fail/blocked。这样证据才完整、缺陷才定位得准；【绝不允许】因为一个子项缺失就跳过其余可测子项、草草判失败。\n"
    "- 【逐项充分验证】当预期是“所有必填项都校验/都提示”这类，要逐个必填项核对：提交→看缺哪个必填项的提示→补一个→再提交，循环直到覆盖所有必填项，不能只验证一个就判 pass；\n"
    "- 【主动探索·必须看全】判定前必须把页面探索完整：向下滚动直到页面底部、横向滚动看完表格/列表的全部列(表格右侧常有更多列被遮住)；"
    "在判断“某字段/某列缺失、不符”之前，务必已经横向滚到表格最右端、纵向滚到底，多次尝试仍找不到才算缺失，不能只看首屏就下结论；"
    "目标控件不在当前屏就滚动/返回/换路径去找，遇到阻碍想办法绕过(不同入口、先建数据再核对)，不要浅尝就放弃；\n"
    "- 【先等数据加载再判断】若列表/表格在加载中、显示骨架屏，或显示“共0条/暂无数据”但很可能只是还没加载完(例如刚进入页面、刚点查询)，"
    "先输出 action=wait 等待，再重新观察后判断；绝不在数据尚未稳定时就下“为空/缺失”的结论；\n"
    "- 【计数说“共N台/共N条”(N>0)但下方明细还没出现＝还在异步加载，必须多等再看，别当缺失】当页面出现“符合条件设备共 34 台”“共 N 条/项”这类【计数且 N>0】，"
    "但其下方对应的【明细列表/设备行/记录行还没渲染出来】(尤其是刚选完项目/筛选/刚进页，列表常延迟几秒才加载)——这【几乎一定是异步加载未完成，不是没有数据】。"
    "此时必须【连续 wait 多等几次(可 2~4 次)、并重新下滑到该区域重新看】，直到明细行真正出现再核对其内容；"
    "【绝不允许】在“计数>0 但明细行尚未出现”时就判“无明细/设备未展示/数据缺失/缺陷”——那是把加载延迟误当成缺陷。只有多等多刷后明细仍始终不出现，计数与明细才算真不一致、才考虑判问题；\n"
    "- 【“查看列表/明细列表字段展示”类步骤——就停在【列表卡片页】上读字段，绝不钻进单条详情/验机页】当本步是“查看XX列表卡片/明细列表的全部字段”“核对某字段在列表的展示”这类【只看列表里每条记录展示了哪些字段、展示对不对】的步骤："
    "目标页面就是那个【列表/明细列表页本身】——每条记录以【卡片/行】的形式，把要核对的字段(车型、出厂编号、自编码、品牌/型号、验机状态、原因等)【直接铺在列表卡片上】。你要做的就是【停在这个列表页，直接读卡片上的字段】来核对。"
    "【绝不要】去点列表里的某一条记录、钻进它的【单条详情页/验机页/编辑页】——那是另一个【更深的页面】(反例：实测里把“明细”误解成点某台设备→进了“验机”详情页)，既不是本步要核对的“列表字段展示”，还会丢失列表上下文、越走越偏、最终判不出来。"
    "步骤里的“明细/明细列表”一律指【设备明细列表(列表页本身)】，【不是】“点进某一台设备去看它的明细/详情”。"
    "只有当【列表卡片上确实找不全要核对的字段】、且已多滚动多刷新仍缺时，才据此判该字段在列表未展示；【绝不能】靠钻进单条详情去补看字段——那样即便详情里有，也证明不了“列表卡片展示了该字段”；\n"
    "- 【多字段搜索/过滤类步骤·三条铁律】当本步是“按某字段搜索/模糊匹配/实时过滤”这类：\n"
    "  ①【先清空→取第一张卡片的值→切字段→输入】顺序很重要：\n"
    "    a) 先【彻底清空搜索框】(点搜索框右侧的×，或删掉已有关键字)，让列表【恢复显示全部资产】——"
    "上一步搜过后框里常残留旧关键字，不清空的话切了字段就是“暂无数据”、也读不到值；\n"
    "    b) 从【当前列表第一张卡片】上读取【本步目标字段】的值(名称/资源编码/资源编码各读各的)；\n"
    "    c) 把搜索框左侧【字段下拉】切换成本步指定的字段，确认下拉当前值就是该字段；\n"
    "    d) 在搜索框输入上面取到的值。【取值规则】：默认取第一张卡片该字段的【完整值】(如资源编码就整串 AS2026000012)；"
    "只有步骤【明确说“模糊/片段/部分”匹配】时，才取该完整值的【一段】。绝不用所有卡片都含的通用前缀(那样看不出过滤)，"
    "也绝不把不同的码混为一谈(资源编码≠资源编码/资源编码)；\n"
    "  ②【判定必须核对搜索框里的值】judge 时【必须先确认搜索框里显示的就是本步骤要搜的那个值】：若搜索框为空、或里面不是本步该搜的值，"
    "则本步尚未真正执行到位，【不得判 pass】——先(重新)切对字段并输入本步的值，再判；\n"
    "  ③【判定必须核对结果确实被过滤】光搜索框里有值【还不够】：必须确认【列表结果确实按该关键字过滤了】"
    "(可见结果应基本都在指定字段命中该关键字)。输入后若没实时过滤，先按上一条【主动触发搜索(回车/搜索键/搜索按钮)】；"
    "在【已确实触发过搜索】之后，列表若【仍是全量条数】(顶部“共N件”没变)、或可见条目里【还有明显不含该关键字的项】"
    "(如搜“液压”结果里还出现“平衡阀组件”“上调平油缸密封圈”这类不含“液压”的资产)，才说明过滤没生效、与预期不符，判 fail(缺陷)；"
    "反之若触发后正确筛出才判 pass。【绝不能只因为搜索框里填了值就判 pass】；\n"
    "  ④【判定截图要留住关键字】下 judge 前保证刚输入的关键字仍留在搜索框中(没被清空/被上一步的值覆盖)，让判定依据的这一屏能同时看到【搜索框里的关键字】和【过滤后的结果】；\n"
    "- 【判定锚点】若给了本步骤的判定锚点(check_points)，judge 时必须【逐条】对照当前界面核对，在 checks 里给出每条 {point, ok}；"
    "只有所有【应满足】的锚点都 ok=true 才可 pass；有应满足的锚点 ok=false → 据其性质判 fail(与预期不符)或 blocked(无数据/到不了)，并在 reason 说明；\n"
    "- 当确已完成本步骤验证时输出 action=judge 并给 verdict：\n"
    "  pass=锚点全部满足且符合预期；"
    "fail=【确已进入目标页面】但功能/数据/字段与预期不符(真正的产品缺陷)；"
    "blocked=无法验证——如没能进入/找不到目标页面、进错子系统未能纠正、目标页无数据、反复尝试仍到不了。\n"
    "【关键】‘没进对页面/没找到入口’属于 blocked(无法验证)，绝不能判 fail——fail 只用于真的进到了正确页面、看清内容后确认不符；不确定是否进对页面时判 blocked。\n"
    "- 【严格照预期判，不凭空加要求】判定【只依据本步骤给出的『预期』与判定锚点】，不得自行发明预期里没有的要求。"
    "只要『预期』所述达成即判 pass——例如预期是“成功打开时间选择器”，那只要选择器打开了就 pass，"
    "至于它是“年月日时分五列”还是“日期列+时间列(如15:00/15:30)”这类【预期没规定的呈现形式】，一律不作为不符/缺陷依据；"
    "同理不要因“布局/列数/样式/字段个数和我设想的不一样”而判 fail。真判 fail 必须是【预期明确要求的点】与实际不符。\n"
    "【操作类步骤必须真做了才算 pass】：当本步『预期』是“创建/新建/提交/保存/删除/编辑…成功”这类【操作结果】时，"
    "必须【确实执行了该操作】——打开新建/编辑表单 → 按步骤填写必填项(资源名称/数量/仓库等) → 点提交/保存 → "
    "看到成功证据(成功提示/toast、列表里出现新记录、状态变化)——才判 pass。【仅停在列表页/入口页、没真正打开表单、"
    "没填写、没提交】的，判 blocked(未能完成操作)并在 reason 说明卡在哪一步，【绝不】因为“到了相关的列表/入口页面”"
    "就判 pass。导航到了列表只是第一步，不等于完成了新建。\n"
    "  · 【填表要填全每个必填项，别漏“数量”这种】新建/编辑表单里，选了“资源/商品”后通常还要【单独设置它的数量】"
    "(数量默认常是 0/空，是必填)；只选资源没填数量就点提交，会被拦下、建不出来。提交前【逐一确认每个必填项都已"
    "有值】(如 业务单据：资源名称√ 数量√(>0) 仓库√)，再点提交。\n"
    "  · 【点了提交≠成功，要确认真的建出来了】点提交/保存后，若【仍停在表单页】、或冒出【校验提示/错误 toast】"
    "(如“请填写数量”“数量必须大于0”“仓库不能为空”)，说明【没提交成功、有必填项没填】——就【回去把缺的填上再提交】，"
    "【不要】当成已完成。只有【离开表单、且列表/详情里出现一条创建时间=当前(刚刚)的新记录】才算真成功。\n"
    "  · 【核对“刚创建的记录”前，先【下拉刷新列表】】很多 App 提交成功后返回列表【不会自动刷新】：列表项还是旧缓存"
    "(顶部仍是旧单)、但页顶“共N单”的计数其实已经+1——这会让你误判“没建成功”。所以要找自己刚建的那条前，"
    "【先在列表顶部往下拉一下刷新】(可看“共N单”数字有没有变大来确认建没建)，刷新后再找【创建人=本账号、"
    "创建时间=今天且刚刚】的那条来核对。别因为“列表没自动显示”就直接判没创建。\n"
    "  · 【“回到列表”≠“提交成功”】取消/返回/被拦截也会回到列表页。必须看到【明确的成功证据】：成功提示/toast，"
    "或列表最顶部【多出一条本账号刚刚创建的新记录】(单号已生成、创建人=当前账号、创建时间=刚刚)。只凭“表单消失、"
    "回到了列表”就判 pass 是错的。\n"
    "  · 【提交后第一件事：把本次新建的单号记下来(最可靠)】点了提交/保存后，【立刻】读出本次新建记录的单号/编号"
    "(如 IN2026…)——通常在成功提示/提交后的结果页/详情页上。把单号写进 reason，后续所有核对都【只认这个单号】。\n"
    "  · 【单号/创建时间要和“当前真实时间”对得上，否则就不是你建的】提示词开头给了【当前真实时间】。"
    "很多单号本身就带日期(如 IN【20260717】…)。若你读到的单号日期/创建时间【不是今天、不在刚刚几分钟内】"
    "(反例：当前是 2026-07-17，你却报单号 IN【20260714】…、或说“创建时间刚刚(2026-07-14 10:41)”)，"
    "那它【必定是几天前的旧单】，【绝不是】你这次创建的——说明你其实【没提交成功】(或没提交)。"
    "此时【不许】把它当成自己的成果判 pass：要么回去把提交真正做完，要么判 blocked 说明“未生成本次新建记录”。\n"
    "  · 【拿不到单号时才退而取“列表最顶部那条”，且只用“身份信息”认领】列表按创建时间倒序、最新的在最顶部；"
    "但测试环境多人共用，最顶部那条【可能是别人(并发测试/同事)刚建的】。所以认领它时【只比对身份类信息】："
    "【创建人 = 当前登录账号】且【创建时间 = 就在刚刚】。两者对不上就【不能】认领——继续找或判 blocked 说明"
    "“未能定位到本次创建的记录”，绝不把别人的单当成自己的成果。\n"
    "  · 【认领记录时【绝不能】用资源/仓库这些“待核对字段”去挑】：资源名称/数量/仓库正是本用例要【检查】的内容，"
    "若拿它们去找“和我填的一致”的那条，等于【先筛掉不一致的、再宣布一致】——这样永远发现不了“存进去的资源/仓库不对”"
    "这种真缺陷(自证自话)。认领只用【单号】(最佳)或【创建人+创建时间】(退路)；认领到之后，再去核对资源/仓库/数量"
    "对不对——【先定位、后核对】，两件事不能混。\n"
    "  · 【只核对“自己刚创建的那条”】后续步骤只认上面认领到的那条，【绝不】去翻列表中间/下面的旧单冒充"
    "(例：你新建时选的是“液压油管”，却去核对一条显示“万用表资源”的旧单)。定位不到就判 blocked，不要拿旧数据凑 pass。\n"
    "  · 【找不到单号时，按“本账号+最近创建”去找，绝不按“内容像不像”去找】后续步骤要找“本用例刚创建的那条”而"
    "【前序结论里没给出单号】时：就在列表里找【创建人 = 当前登录账号】且【创建时间最新(就在刚刚)】的那条"
    "(列表按创建时间倒序时，即最顶部那条属于本账号的记录)。"
    "【严禁按“内容/业务相关性”去挑】——反例(实测出过的错)：本用例是关于资源的，就跑去列表里找一张"
    "“看起来与资源相关”的单子点进去；那挑到的必然是【别人的或以前的旧单】，不是你刚建的。"
    "记住：定位靠【身份(谁建的、什么时候建的、单号)】，不靠【内容像不像这条用例】。\n"
    "【数据不满足就换一条，别急着 blocked】：若本步校验需要满足特定条件的数据才能验证——如判排序需≥2条记录、"
    "需要目标字段非空、需要含多个值的记录——而【当前这条数据不满足】(例：进的详情里只有1条使用记录没法判倒序、"
    "目标字段为空)，【不要立刻判 blocked】。先 back 返回列表/上一层，换列表里【下一条】数据重新进入再核对，"
    "如此依次最多试 5 条；直到某条满足即按它判定，5 条都不满足才判 blocked，并在 reason 里说明"
    "“已试N条数据均无满足条件的记录”。\n"
    "  · 【必须换成真正不同的那条】列表里多条常常【名称一样】(如都叫“液压油管”)，若每次都点同名的第一条，"
    "就会原地打转、白烧步数。换记录要按【唯一标识】区分——用资源编码/单号(如 AS2026000025→换成 AS2026000024)、"
    "或明确点列表里【第2条、第3条…】(位置不同)，别再点回刚才那条。\n"
    "  · 【优先挑状态上可能有该数据的那条】：如要看“使用记录”，就别挑“待入库”这种明显还没被使用过的资产(必然没记录)，"
    "优先挑“已入库/在用”等状态的；要看某字段的值，就挑该字段【非空】的那条。\n"
    "  · 【同一原因连续不满足就提前收】：若换了 2~3 条都因【同一个原因】不满足(如都没有使用记录)、且列表里数据状态"
    "看起来一致(如清一色“待入库”、目标字段清一色“--”)，就【提前判 blocked】并说明“测试数据缺失：列表中资产均无XX”，"
    "不必硬凑满 5 条把步数烧光——凑满反而导致 20 步耗尽仍无结论。\n"
    "【重要纪律】没有把预期要求的内容/锚点都核对完之前不要判 pass；没有任何可核对内容时才判 blocked；绝不臆断，也绝不加戏。"
)

_TARGET_TAP_SYSTEM = (
    "你是手机界面定位助手。目标是从截图中找到指定中文文案对应的可点击入口中心点。"
    "只输出一个 JSON，不要解释："
    '{"action":"tap|not_found","x":数,"y":数,"reason":"简短依据"}。'
    "规则："
    "1) 只找与目标文案完全对应的入口，不要找相似文案；"
    "2) 如果目标文案在卡片里，优先返回整张卡片的中心点，而不是只点文字边缘；"
    "3) 如果看不清、找不到、或无法确认，就输出 action=not_found。"
)

_PAGE_READY_SYSTEM = (
    "你是手机测试前置检查助手。目标：判断当前截图是否已经处于本条用例首步要操作/验证的目标页面。"
    "只输出一个 JSON，不要解释："
    '{"state":"ready|not_ready","reason":"简短依据"}。'
    "规则："
    "1) 只有在截图中已经能看到与首步目标高度一致的页面/区域时，才能输出 ready；"
    "2) 如果当前是登录页、桌面、其它业务页、或者看不清是否已在目标页，都输出 not_ready；"
    "3) 不要求目标控件必须正好露出，但页面上下文必须明显匹配。"
)

_VERDICT_CN = {"pass": "通过", "fail": "不符(缺陷)", "blocked": "无法验证"}


async def _is_ready_for_case(d, provider, title: str, first_action: str, first_expected: str) -> tuple[bool, str]:
    try:
        img = await asyncio.to_thread(d.screenshot)
        b64, sw, sh, _ = _encode(img)
        user = (
            f"测试用例：{title}\n"
            f"首步操作：{first_action}\n"
            f"首步预期：{first_expected}\n"
            f"请判断当前截图是否已经处于首步目标页面。当前截图宽{sw}高{sh}像素。"
        )
        raw = await provider.text_multi(
            _PAGE_READY_SYSTEM, user, [(b64, "image/jpeg")], 800,
            reasoning_effort=_reasoning_effort(),
        )
        from app.agents.llm import _extract_json
        act = _extract_json(raw)
        ready = (act.get("state") or "").lower() == "ready"
        return ready, str(act.get("reason") or "")
    except Exception as e:
        return False, f"预判失败：{e}"


class AndroidAgentRunner(BaseRunner):
    platform = "android"
    requires_device = True

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        # 同一时刻只允许一条 App 用例驱动真机，其余排队等待(设备只有一块屏)
        async with _DEVICE_LOCK:
            return await self._drive(case, ctx)

    async def _drive(self, case: Any, ctx: RunContext) -> RunOutcome:
        t0 = time.monotonic()
        apk_install_ok: bool | None = None
        app_launch_by_package_ok: bool | None = None
        try:
            import uiautomator2 as u2
        except Exception as e:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"未安装 uiautomator2，无法直连真机执行：{e}", failure_type="env_error")
        _patch_u2_tolerant_check_alive()

        serial = ctx.device_udid or _first_device_serial()
        if not serial:
            return RunOutcome(status="error", duration_ms=0,
                              error_message="未检测到已连接的安卓真机(adb devices 为空)", failure_type="env_error")
        try:
            # 加超时：uiautomator2 首次连机要往手机推/起服务(全新设备首次接入常见)，卡住时别无限等。
            # 期间还会做探活重试(短暂网络抖动)，见 _patch_u2_tolerant_check_alive 的说明。
            d = None
            for attempt in range(4):
                try:
                    d = await asyncio.wait_for(asyncio.to_thread(u2.connect, serial), timeout=90)
                    dev_w, dev_h = await asyncio.wait_for(asyncio.to_thread(d.window_size), timeout=30)
                    break
                except asyncio.TimeoutError:
                    raise
                except Exception:
                    d = None
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2)
            # 点亮屏幕 + USB 期间保持常亮 + 尝试解锁，避免截到黑屏(灭屏/锁屏)
            try:
                await asyncio.to_thread(d.screen_on)
                await asyncio.to_thread(lambda: d.shell(["svc", "power", "stayon", "true"]))
                await asyncio.to_thread(d.unlock)
            except Exception:
                pass

            # App 换测试包(可选)：ctx.extra["apk"]={source,package} 时，执行用例前在本机(worker/Sonic
            # 后端)先卸旧包再装新包。三条设备路径都经本 Runner，故统一在此处理。装包失败则直接报错。
            _apk = (ctx.extra or {}).get("apk") if isinstance(ctx.extra, dict) else None
            if _apk and _apk.get("source"):
                from app.services.apk import install_apk
                # 传入已连的 u2 设备 d：部分 OEM(华为等) pm install 会弹图形安装确认框并阻塞，
                # install_apk 用 d 自动点掉确认框；否则远程无人值守会卡死到会话超时。
                ok, msg = await asyncio.to_thread(
                    install_apk, serial, _apk.get("source"), _apk.get("package"), d
                )
                apk_install_ok = bool(ok)
                if not ok:
                    return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                      error_message=f"更换测试包失败：{msg}", apk_install_ok=False)
                # 成功也落一行日志：否则「装成功」只能靠"没报错+进了登录"反推，排障时看不直观
                logger.info("更换测试包成功：serial=%s %s", serial, msg)

            # 定位到目标 App（枚举「端→应用包名」配置），让 AI 一上来就在正确 App 里、免桌面找图标。
            # 【关键：先判后重启】批量连续执行时，先判断当前是否【已在本条用例目标页】——是则复用上一条
            # 留下的页面(免重启、免重走整段导航)；否则(或换包重装后)才强制重启目标 App 到干净态。
            # 之前是无条件 app_start(stop=True) 先把 App 强杀回首页，再判就绪 → 永远判"不在目标页"、
            # 永远重启，就绪判断形同虚设、跨用例页面无法复用。
            _app_pkg = (ctx.extra or {}).get("app_package") if isinstance(ctx.extra, dict) else None
            _reinstalled0 = bool(_apk and _apk.get("source"))
            _steps0 = (ctx.extra or {}).get("steps_override") or getattr(case, "steps", None) or [{"action": getattr(case, "title", ""), "expected": getattr(case, "expected_result", "") or ""}]
            _fs0 = _steps0[0] if _steps0 else {}
            # 安全闸：仅当【本执行本设备已登录过】(登录/租户已校验)或【本就不需自动登录】时才允许复用页面，
            # 避免首条用例误复用上次执行遗留页面而跳过登录/租户校验。
            _login_cfg0 = (ctx.extra or {}).get("app_login") if isinstance(ctx.extra, dict) else None
            _reuse_eligible = (not _login_cfg0) or ((ctx.execution_id, serial) in _LOGGED_IN)
            reuse_page = False
            if _app_pkg and not _reinstalled0 and _reuse_eligible:
                try:
                    from app.agents.llm import get_provider as _gp0
                    _rdy0, _rr0 = await _is_ready_for_case(
                        d, _gp0(), getattr(case, "title", ""), _fs0.get("action", ""), _fs0.get("expected", ""))
                    if _rdy0:
                        reuse_page = True
                        app_launch_by_package_ok = True
                        logger.info("执行前已在本条目标页，复用页面、跳过重启/登录/冷启动：case=%s reason=%s",
                                    getattr(case, "id", "case"), _rr0)
                except Exception as e:  # noqa: BLE001 判断失败就当作不在目标页，走正常重启
                    logger.info("执行前就绪预判失败(按需重启)：%s", e)
            if _app_pkg and not reuse_page:
                try:
                    # use_monkey=True：用 LAUNCHER 意图启动，兼容"应用包名≠主Activity包名"的 App
                    # (如Android App com.example.demo.stj 主Activity 是 com.sostarjob.hatch.MainActivity)；
                    # u2 默认按同包名找主Activity会解析失败、App起不来落桌面。
                    await asyncio.to_thread(lambda: d.app_start(_app_pkg, stop=True, use_monkey=True))
                    await asyncio.sleep(2)
                    app_launch_by_package_ok = True
                except Exception as e:
                    app_launch_by_package_ok = False
                    logger.info("按包名启动 App(%s) 失败，退回 AI 桌面查找：%s", _app_pkg, e)
        except asyncio.TimeoutError:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"连接真机超时({serial})：uiautomator2 初始化卡住，请检查手机USB调试授权/数据线，或手机上是否弹出安装确认", failure_type="env_error")
        except Exception as e:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"连接真机失败({serial})：{e}", failure_type="env_error")

        # App 自动登录（可选）：装包/启动后、跑用例前，把 app 登进指定环境/账号/租户，
        # 登录态留在设备的 app 里供后续用例复用。同一执行同一设备只登一次；换包(重装)会清登录 → 强制重登。
        app_login_cfg = (ctx.extra or {}).get("app_login") if isinstance(ctx.extra, dict) else None
        if app_login_cfg and not reuse_page:  # 复用页面=已登录且在目标页，无需再登
            try:
                from app.services.runners import app_login as _login
                from app.agents.llm import get_provider as _gp
                _match = next((p for p in (getattr(case, "platforms", None) or []) if p in app_login_cfg), None)
                if _match:
                    _cfg = app_login_cfg.get(_match) or {}
                    _reinstalled = bool(_apk and _apk.get("source"))
                    _lk = (ctx.execution_id, serial)
                    if await _login.supports(_match, _cfg.get("label", "")):
                        execution_control.log(ctx.execution_id, f"正在自动登录 App（{_cfg.get('label') or _match}），首次连接真机稍慢，请稍候…", case_id=getattr(case, "id", None))
                        _ok, _msg = await _login.run_login(
                            d, dev_w, dev_h, _gp(), _app_pkg,
                            platform_key=_match, label=_cfg.get("label", ""),
                            env=_cfg.get("env", ""), phone=_cfg.get("account", ""),
                            tenant=_cfg.get("tenant") or None, code=_cfg.get("code") or None,
                        )
                        if _ok:
                            _LOGGED_IN.add(_lk)
                            execution_control.log(ctx.execution_id, "登录完成，开始执行用例步骤", case_id=getattr(case, "id", None))
                        else:
                            logger.warning("App 自动登录失败(%s)：%s（已中止执行，避免卡在登录页）", _match, _msg)
                            return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                              error_message=f"App 自动登录失败，已中止执行：{_msg}")
            except Exception as e:  # noqa: BLE001 登录异常(非登录失败)不阻断执行
                logger.warning("App 自动登录异常：%s", e)

        async def _relogin() -> bool:
            """中途掉登录(会话过期/被踢到登录页)时重登：复用同一套登录配方+轨迹回放，
            而不是让 AI 在登录页干瞪眼(它拿不到账号/验证码, 一键登录又常不支持)。"""
            if not (app_login_cfg and _app_pkg):
                return False
            try:
                from app.services.runners import app_login as _login
                from app.agents.llm import get_provider as _gp
                _m = next((p for p in (getattr(case, "platforms", None) or []) if p in app_login_cfg), None)
                if not _m:
                    return False
                _c = app_login_cfg.get(_m) or {}
                if not await _login.supports(_m, _c.get("label", "")):
                    return False
                _o, _m2 = await _login.run_login(
                    d, dev_w, dev_h, _gp(), _app_pkg,
                    platform_key=_m, label=_c.get("label", ""), env=_c.get("env", ""),
                    phone=_c.get("account", ""), tenant=_c.get("tenant") or None, code=_c.get("code") or None,
                )
                return bool(_o)
            except Exception as _e:  # noqa: BLE001
                logger.info("中途重登异常：%s", _e)
                return False

        # 数据前置注入好的步骤（方案 §20）优先，否则用例原步骤。
        steps = (ctx.extra or {}).get("steps_override") or getattr(case, "steps", None) or [
            {"action": getattr(case, "title", "执行用例"), "expected": getattr(case, "expected_result", "") or ""}
        ]
        title = getattr(case, "title", "")
        case_id = getattr(case, "id", "case")
        # AI 质量闭环：下发覆盖项，判定时回标 item_id（web/android 共享 helper）
        from app.services.runners import coverage_evidence
        covered_items = getattr(case, "covered_items", None)
        cov_hint = coverage_evidence.covered_items_hint(covered_items)

        # 目标 App 约束：告诉 AI 本用例只能在这个 App 里操作，避免它迷路后乱开别的 App(如把Android App跑进业务端工作台)
        _apps = [str(p) for p in (getattr(case, "platforms", None) or []) if str(p).strip()]
        app_hint = (
            f"【本用例只在「{'、'.join(_apps)}」App 内进行】你只能在该 App 里操作；"
            f"若发现当前在手机桌面、或进了别的 App(如业务端工作台/Android App/物流等)，只能【打开或回到「{'、'.join(_apps)}」】再继续，"
            "绝不要用其它 App 去完成本步骤，也不要因为名字相似(如都带工作台/联合)就进错 App。\n\n"
        ) if _apps else ""

        from app.agents.llm import get_provider, _extract_json
        provider = get_provider()
        shot_i = 0
        # 说明：是否重启/复用页面已在前面「先判后重启」处一次性决定（reuse_page）——
        # 复用则维持上一条页面，否则已强制重启到干净首页。这里不再二次冷启动，避免无谓重启。

        def _save(png: bytes) -> str | None:
            nonlocal shot_i
            url = _save_shot(png, ctx.execution_id, case_id, shot_i)
            shot_i += 1
            return url

        ui_trace: list[dict] = []
        run_error: str | None = None

        for i, step in enumerate(steps, start=1):
            s_action = step.get("action", "")
            s_expected = step.get("expected", "")
            step_targets = _step_text_targets(s_action, s_expected)
            s_checks = step.get("check_points") or []     # 该步判定锚点
            checks_result: list[dict] = []                # AI 逐条核对结果
            shot: str | None = None      # 本步骤一张结果截图(判定时刻)
            notes: list[str] = []
            verdict, reason = None, ""
            prev_png, same_count = None, 0   # 卡死检测：连续相同截图计数
            last_intent: set = set()         # 上一 tap 的意图目标(从reason抽)：连续两次同意图→上次没点中→改兜底
            tried_intents: set = set()       # 已兜底过的意图(避免对同一目标反复兜底空转)
            act_idx = 0                      # 步内动作序号(仅日志)
            popup_tries = 0                  # 本步关更新弹窗次数上限，防止点×没关成而空转
            relaunch_tries = 0               # 本步"离开目标App→monkey重拉"次数上限，防死循环
            native_popup_tries = 0           # 本步关系统原生弹窗(权限/引导)次数上限，防死循环
            recorded: list[dict] = []        # 本步实际执行的操作序列(判通过则沉淀为"操作经验")
            replayed_recipe = False          # 本步是否回放过已有经验(回放过就不再覆盖旧经验)
            hang_tries = 0                   # 本步远程/AI 调用超时次数上限，防挂死几分钟又防死循环
            ocr_tries = 0                    # 本步 OCR 优先定位次数上限(纯控 CPU/延迟, 与 token 无关)
            relogin_tries = 0                # 本步"检测到掉登录→重登"次数上限，防死循环
            photo_upload_step = _step_needs_photo_picker_context(s_action, s_expected, s_checks)
            photo_picker_active = False

            async def _tap_by_text(labels: list[str]) -> str | None:
                for label in labels:
                    for factory in (
                        lambda t=label: d(text=t),
                        lambda t=label: d(textContains=t),
                        lambda t=label: d(description=t),
                        lambda t=label: d(descriptionContains=t),
                    ):
                        try:
                            obj = factory()
                            if not await asyncio.to_thread(lambda: obj.exists):
                                continue
                            await asyncio.to_thread(obj.click)
                            return f"按文案点击「{label}」"
                        except Exception:
                            continue
                return None

            async def _tap_by_text_once() -> str | None:
                return await _tap_by_text(step_targets) if step_targets else None

            async def _ocr_tap(labels: list[str]) -> str | None:
                """OCR 定位点击：在当前截图上 OCR 找到【确切文案】的文字框中心并点它——
                自绘 App 里控件不在无障碍树、AI 坐标又不准时，靠 OCR 精准点字。引擎不可用则返回 None。"""
                if not app_ocr.available():
                    return None
                try:
                    _img = await asyncio.to_thread(d.screenshot)
                except Exception:
                    return None
                for label in labels:
                    pt = await asyncio.to_thread(app_ocr.locate_text, _img, label)
                    if pt:
                        await asyncio.to_thread(d.click, int(pt[0]), int(pt[1]))
                        return f"OCR定位点击「{label}」({pt[0]},{pt[1]})"
                return None

            async def _confirm_photo_picker_after_selection(desc: str) -> str | None:
                """照片选择器里点了缩略图后，主动点完成/确定把图片带回业务表单。"""
                nonlocal photo_picker_active
                if not (photo_upload_step and photo_picker_active):
                    return None
                if _is_photo_picker_confirm_text(desc):
                    photo_picker_active = False
                    return None
                if "选择照片" in desc and not _is_photo_thumbnail_selection_text(desc):
                    return None
                rec = await _tap_by_text(list(_PHOTO_PICKER_CONFIRM_HINTS))
                if rec is None:
                    rec = await _ocr_tap(list(_PHOTO_PICKER_CONFIRM_HINTS))
                if rec:
                    photo_picker_active = False
                    return f"{rec}（选图后确认带回表单）"
                return None

            async def _recover_from_photo_preview_if_needed(desc: str) -> str | None:
                """预览页底部“选择”通常只是勾选开关；勾上后回网格再找完成按钮。"""
                nonlocal photo_picker_active
                if not (photo_upload_step and photo_picker_active and _is_photo_preview_select_toggle_text(desc)):
                    return None
                await asyncio.sleep(0.5)
                try:
                    cur_pkg = ((await asyncio.to_thread(d.app_current)) or {}).get("package") or ""
                except Exception:
                    cur_pkg = ""
                if _app_pkg and cur_pkg == _app_pkg:
                    photo_picker_active = False
                    return None
                try:
                    await asyncio.to_thread(d.press, "back")
                    await asyncio.sleep(0.5)
                except Exception:
                    return "预览页点“选择”后仍未回业务页，尝试返回照片网格失败"
                rec = await _tap_by_text(list(_PHOTO_PICKER_CONFIRM_HINTS))
                if rec is None:
                    rec = await _ocr_tap(list(_PHOTO_PICKER_CONFIRM_HINTS))
                if rec:
                    photo_picker_active = False
                    return f"预览页点“选择”后返回照片网格；{rec}（确认带回表单）"
                return "预览页点“选择”后返回照片网格，等待点击完成/确定带回表单"

            async def _native_or_relocate(_b64: str, extra_targets: list[str] | None = None) -> str | None:
                """点空兜底：目标 = 模型这次意图里抽到的控件(extra_targets，如"现场处理") + 步骤目标。
                ①u2 原生按文案点击(控件在无障碍树里时零误差)；
                ②OCR 定位点击(自绘 App 里精准点字)；
                ③都不行→「专注定位」模型只为该目标重估坐标再点。"""
                labels = list(dict.fromkeys((extra_targets or []) + step_targets))
                if not labels:
                    return None
                rec = await _tap_by_text(labels)
                if rec:
                    return rec
                rec = await _ocr_tap(labels)   # OCR 精准点字(自绘 App)
                if rec:
                    return rec
                primary = _primary_tap_target(labels)
                if not primary:
                    return None
                try:
                    lr = await provider.text_multi(
                        _TARGET_TAP_SYSTEM,
                        f"目标文案：{primary}\n请在当前截图中定位这个控件/选项，返回应点击的中心坐标。",
                        [(_b64, "image/jpeg")], 800, reasoning_effort=_reasoning_effort(),
                    )
                    la = _extract_json(lr)
                    if (la.get("action") or "").lower() == "tap":
                        ddx = int(la.get("x", 0) * scale_x)
                        ddy = int(la.get("y", 0) * scale_y)
                        await asyncio.to_thread(d.click, ddx, ddy)
                        return f"专注定位点击「{primary}」({ddx},{ddy})"
                except Exception:
                    return None
                return None

            # ── App 导航加速：路径缓存(①) + 原生 scroll.to 直达(②) ──
            # 本步是"箭头链导航"型(如 工作台→资源列表)时：先注入上次成功路径的提示给 AI；
            # 再用原生逐段直达试一把，能到目标就省掉 AI 盲滑。仅对有目标包名的 App 用例生效。
            from app.services.runners import app_nav
            _nav_path = app_nav.nav_path_from_step(s_action, s_expected)
            _nav_goal = _nav_path[-1] if _nav_path else None
            nav_hint = ""
            _reached = 0
            if _app_pkg and _nav_goal:
                nav_hint = app_nav.hint(await app_nav.load(_app_pkg, _nav_goal))
                try:
                    _reached, _nnote = await app_nav.native_navigate(d, _nav_path)
                    if _nnote:
                        notes.append(_nnote)
                        logger.info("原生导航直达 %d/%d 段：case=%s %s", _reached, len(_nav_path), case_id, _nnote)
                        execution_control.log(ctx.execution_id, f"原生导航：{_nnote}", case_id=getattr(case, "id", None))
                except Exception as _e:  # noqa: BLE001 原生导航失败不阻断，回退视觉
                    logger.info("原生导航异常(回退视觉)：%s", _e)
                # ── OCR 滚动直达：原生没走完的段，逐段【下滑+OCR找该文案，找到即点】。
                # 确定性、每次一样、纯本地零 token、不靠 AI 眼看每屏——解决"深层入口(如底部资源列表)时好时坏"。
                if _reached < len(_nav_path) and app_ocr.available():
                    try:
                        _nav_ok = True
                        for _lbl in _nav_path[_reached:]:
                            _hit = False
                            for _sc in range(26):
                                if await _tap_by_text([_lbl]):     # 原生能点(底部导航等)直接点
                                    _hit = True
                                    break
                                _im = await asyncio.to_thread(d.screenshot)
                                _pt = await asyncio.to_thread(app_ocr.locate_text, _im, _lbl)
                                if _pt:
                                    await asyncio.to_thread(d.click, int(_pt[0]), int(_pt[1]))
                                    _hit = True
                                    break
                                await asyncio.to_thread(_swipe, d, dev_w, dev_h, "up")  # 下滑继续找
                                await asyncio.sleep(0.5)
                            if not _hit:
                                _nav_ok = False
                                break
                            await asyncio.sleep(1.2)
                        if _nav_ok:
                            notes.append(f"OCR滚动直达「{_nav_goal}」")
                            logger.info("OCR滚动直达「%s」：case=%s", _nav_goal, case_id)
                            execution_control.log(ctx.execution_id, f"OCR滚动直达「{_nav_goal}」", case_id=getattr(case, "id", None))
                    except Exception as _e:  # noqa: BLE001
                        logger.info("OCR滚动导航异常(回退视觉)：%s", _e)
            _nav_swipes_before = 0  # 记录本步为找目标滚了多少次(用于回写缓存 swipes)

            # ── 操作经验回放(③)：本步曾判【通过】→ 回放上次成功的【页内确定性操作】(切字段/输入/回车)。
            # 【只对非导航步骤】：导航(滚动找入口)靠 nav-cache+视觉，位置相关、盲回放坐标/滑动会跑偏。
            # 回放【只按文案点 + 输入 + 回车】：找不到文案就跳过(绝不点 stale 坐标)，不回放滑动/返回。
            _step_sig = app_nav.step_signature(s_action)
            if _app_pkg and not _nav_path:
                _recipe = await app_nav.load_step_recipe(_app_pkg, _step_sig)
                if _recipe:
                    execution_control.log(ctx.execution_id, f"命中操作经验，回放 {len(_recipe)} 步…", case_id=getattr(case, "id", None))
                    logger.info("回放操作经验 %d 步：case=%s step=%d", len(_recipe), case_id, i)
                    for _rec in _recipe:
                        try:
                            _ra = _rec.get("a")
                            if _ra == "tap":
                                _rt = _rec.get("text")
                                if _rt:
                                    if not await _tap_by_text([_rt]):
                                        await _ocr_tap([_rt])   # 原生找不到→OCR 精准点字；再找不到就跳过
                            elif _ra == "input":
                                await _enter_text(d, _rec.get("value", ""), dev_w, dev_h)
                            elif _ra == "enter":
                                try:
                                    await asyncio.to_thread(lambda: d.send_action("search"))
                                except Exception:
                                    await asyncio.to_thread(d.press, "enter")
                            await asyncio.sleep(0.8)
                        except Exception:
                            continue
                    replayed_recipe = True
                    notes.append(f"已按上次成功经验回放 {len(_recipe)} 步操作")

            for _ in range(_MAX_ACTIONS_PER_STEP):
                # 用户取消执行：逐动作检查，尽快中断本条用例（App 每动作数十秒，这里停最及时）
                # 整批取消 或 只取消当前这条用例，都在此尽快停下本条
                if execution_control.is_canceled(ctx.execution_id) \
                        or execution_control.is_case_canceled(getattr(case, "id", None)):
                    run_error = "已取消执行"
                    break
                # 中途掉登录(会话过期/被踢到登录页)→ 用登录配方【重登】，别让 AI 在登录页干耗步数。
                # 每步限 2 次防死循环；只在能读到登录页标志时触发(一次 dump, 便宜)。
                if _app_pkg and app_login_cfg and relogin_tries < 2:
                    try:
                        _lx = await asyncio.to_thread(d.dump_hierarchy)
                        if _lx and any(k in _lx for k in ("登录失效", "重新登录", "验证码登录", "本机一键登录", "请输入手机号", "获取验证码")):
                            relogin_tries += 1
                            logger.info("检测到掉登录，尝试重登：case=%s 步%d", case_id, i)
                            execution_control.log(ctx.execution_id, "检测到登录失效，正在重新登录…", "warn", case_id=case_id)
                            if await _relogin():
                                notes.append("检测到掉登录，已重新登录")
                                await asyncio.sleep(1.0)
                                continue
                    except Exception:
                        pass
                # 离开目标 App(掉桌面/进了别的 App)→ 用 monkey 把目标 App 重新拉回前台，别让 AI 在桌面
                # 靠视觉点图标(不可靠)。每步限 3 次防死循环；系统UI/输入法/权限安装框不算离开、不重拉。
                if _app_pkg and relaunch_tries < 3:
                    try:
                        cur_pkg = ((await _await_to(asyncio.to_thread(d.app_current), 20)) or {}).get("package") or ""
                        if cur_pkg and cur_pkg != _app_pkg and not any(s in cur_pkg for s in _SKIP_RELAUNCH):
                            if photo_upload_step and photo_picker_active:
                                notes.append(f"照片选择器前台({cur_pkg})，保持选图流程，不重拉目标App")
                            else:
                                await asyncio.to_thread(lambda: d.app_start(_app_pkg, use_monkey=True))
                                await asyncio.sleep(2.0)
                                relaunch_tries += 1
                                notes.append(f"检测到已离开目标App(当前{cur_pkg})，已用 monkey 重拉「{_app_pkg}」")
                                logger.info("离开目标App(当前%s)，monkey重拉：case=%s 步%d", cur_pkg, case_id, i)
                                continue
                    except Exception:
                        pass
                try:
                    img = await _await_to(asyncio.to_thread(d.screenshot), 45)
                except asyncio.TimeoutError:
                    hang_tries += 1
                    logger.warning("截图超时(45s)重试第%d次：case=%s 步%d", hang_tries, case_id, i)
                    execution_control.log(ctx.execution_id, f"设备无响应(截图超时)，自动重试 {hang_tries}…", "warn", case_id=getattr(case, "id", None))
                    if hang_tries >= 4:
                        run_error = "设备连续无响应(截图多次超时)，请重试"
                        break
                    await asyncio.sleep(1.0)
                    continue
                except Exception as e:
                    run_error = f"截图失败：{e}"
                    break
                b64, sw, sh, png = _encode(img)
                scale_x, scale_y = dev_w / sw, dev_h / sh

                # 检测到"发现新版本"类更新弹窗 → 点右上角 ×(不点"以后再说")关掉，再重截图继续。
                # 放在截图之后，以便 × 无原生节点时用当前截图做视觉定位。最多试 3 次，防止点×没关成空转。
                if popup_tries < 3:
                    try:
                        _dz = await _dismiss_update_popup(d, provider, b64, scale_x, scale_y)
                        if _dz:
                            popup_tries += 1
                            notes.append(f"关闭版本更新弹窗（{_dz}）")
                            await asyncio.sleep(1.0)
                            continue
                    except Exception:
                        pass

                # 卡死兜底：连续多张画面完全相同且非主动等待，判为无进展，提前结束本步。
                # 注意：状态栏时钟/渲染噪声会让截图不逐字节相同，故此判定不可靠，仅作最后兜底；
                # "点空"识别改用"连点同一处"信号(见下方 tap 分支)，不依赖截图相等。
                same_count = same_count + 1 if (prev_png is not None and png == prev_png) else 0
                prev_png = png
                if same_count >= _STUCK_LIMIT:
                    verdict = verdict or "blocked"
                    reason = reason or "界面连续无变化，疑似卡住，无法继续操作以完成本步骤"
                    shot = _save(png)
                    break

                checks_text = ("\n判定锚点(逐条核对)：\n" + "\n".join(f"- {p}" for p in s_checks)) if s_checks else ""
                # 覆盖项是【用例级】的，只在最后一步下发：早期步骤看到"提交后生成任务"这类尚不可能
                # 满足的覆盖项，会把它当锚点判不通过，把前面步骤误伤成 blocked。
                if i == len(steps):
                    checks_text += cov_hint  # 覆盖项提示（含 item_id），供 AI 回标
                user = (
                    app_hint + nav_hint + _photo_picker_context_hint(photo_upload_step, photo_picker_active)
                    + f"测试用例：{title}\n\n"
                    + _now_hint()
                    + _prior_steps_hint(ui_trace)
                    + f"当前步骤 {i}/{len(steps)}：\n操作：{s_action}\n预期：{s_expected}{checks_text}\n\n"
                    f"本步已执行：\n" + ("\n".join(notes) or "(无)") +
                    f"\n\n当前截图宽{sw}高{sh}像素。请输出下一步操作 JSON，或在可判定时输出 judge+verdict"
                    + ("(并在 checks 里逐条给出锚点核对结果)" if s_checks else "") + "。"
                )
                try:
                    # 超时给到 180s：gpt-5.4 是推理模型, 偶发单次要 120s+；给足让它跑完, 而不是 120s 切断+重试
                    # (重试同样慢, 白白多花一次)。底层 HTTP 超时 600s, 这里 180s 只兜真挂死。
                    raw = await _await_to(
                        provider.text_multi(_SYSTEM, user, [(b64, "image/jpeg")],
                                            _ACTION_MAX_TOKENS, reasoning_effort=_reasoning_effort()),
                        180)
                except asyncio.TimeoutError:
                    hang_tries += 1
                    logger.warning("AI 决策超时(180s)重试第%d次：case=%s 步%d", hang_tries, case_id, i)
                    execution_control.log(ctx.execution_id, f"AI 决策超时，自动重试 {hang_tries}…", "warn", case_id=getattr(case, "id", None))
                    if hang_tries >= 4:
                        run_error = "AI 连续无响应(多次超时)，请重试"
                        break
                    continue
                except Exception as e:
                    run_error = f"AI 决策失败：{e}"
                    break
                act = _extract_json(raw)
                a = (act.get("action") or "").lower()
                act_text = " ".join(str(act.get(k) or "") for k in ("target", "reason", "text"))
                if photo_upload_step and _is_photo_picker_text(act_text):
                    photo_picker_active = True
                act_idx += 1
                logger.info("测试步骤 %d/%d 动作%d：case=%s action=%s reason=%s",
                            i, len(steps), act_idx, case_id, a, str(act.get("reason", ""))[:70])
                execution_control.log(
                    ctx.execution_id,
                    f"步骤{i}/{len(steps)} 动作{act_idx}：{a} — {str(act.get('reason', ''))[:80]}",
                    case_id=case_id,
                )

                # 只在【AI 自己判断当前有系统弹窗】时，才用确定性方式(一次 dump)去关——平时零开销，
                # 不必每个动作都探测。AI 点系统权限/引导弹窗常点不准而空转，这里接管更稳。
                if a == "tap" and native_popup_tries < 4 and any(
                    kw in str(act.get("reason", "")) for kw in _NATIVE_POPUP_HINTS
                ):
                    try:
                        from app.services.runners.app_login import _dismiss_native_popups
                        if await asyncio.to_thread(_dismiss_native_popups, d):
                            native_popup_tries += 1
                            notes.append("已确定性关闭系统弹窗")
                            await asyncio.sleep(0.6)
                            continue
                    except Exception:
                        pass

                if a == "judge":
                    verdict = act.get("verdict") if act.get("verdict") in ("pass", "fail", "blocked") else "blocked"
                    reason = act.get("reason") or ""
                    raw_checks = act.get("checks") if isinstance(act.get("checks"), list) else []
                    checks_result = [
                        {"point": str(c.get("point", "")), "ok": bool(c.get("ok")), "item_id": c.get("item_id")}
                        for c in raw_checks if isinstance(c, dict)
                    ]
                    # 兜底纪律：判 pass 但有锚点未满足 → 降级为 blocked，避免假通过。
                    # 只认【本步自己的锚点】：用例级覆盖项不参与单步成败，否则前面步骤会被后面
                    # 才可能满足的覆盖项误伤成 blocked。
                    own_miss = coverage_evidence.step_own_failed_checks(checks_result, covered_items)
                    if verdict == "pass" and own_miss:
                        miss = "、".join(c["point"] for c in own_miss)
                        verdict = "blocked"
                        reason = (reason + f"；但锚点未满足：{miss}").strip("；")
                    if verdict in ("fail", "blocked") and own_miss:
                        # AI 常把固定底部按钮误当作“已滚到底”。在因字段缺失判失败前，用 OCR 继续滚动
                        # 查找检查点中的字段；找到则说明探索不充分，打回本轮 judge 继续判定。
                        # 同样只按本步锚点回捞，别去满屏找覆盖项文案里的词。
                        labels = _field_labels_from_failed_checks(own_miss)
                        probe = await _scroll_to_missing_check_label(d, labels, dev_w, dev_h)
                        if probe:
                            notes.append(probe)
                            verdict, reason = None, ""
                            checks_result = []
                            await asyncio.sleep(0.5)
                            continue
                    # 本步结果截图：判定期间界面可能已变(成功toast消失/列表刷新完成)，重新抓一张【当前最新】的存，
                    # 别用循环开头那张旧的(否则存的证据图和真实结果对不上、看着像"没刷新")。
                    try:
                        _, _, _, _png_now = _encode(await asyncio.to_thread(d.screenshot))
                        shot = _save(_png_now)
                    except Exception:
                        shot = _save(png)
                    # 导航步骤判通过 → 回写导航缓存(①)：记下入口/路径/到目标滚了几次，供下次提示+直达
                    if verdict == "pass" and _app_pkg and _nav_goal:
                        try:
                            await app_nav.save(
                                _app_pkg, _nav_goal,
                                entry=(_nav_path[-2] if len(_nav_path) >= 2 else None),
                                path=_nav_path, swipes=_nav_swipes_before, direction="up",
                            )
                        except Exception:
                            pass
                    # 步骤判通过 → 沉淀"操作经验"(③)：把本步成功的页内确定性操作(切字段/输入/回车)记下。
                    # 仅【非导航步骤】且【本次靠 AI 自己跑通、非回放】的干净成功才记(导航靠 nav-cache；
                    # 回放的不再回写覆盖好经验)。recorded 里已不含滑动/纯坐标点(不可靠回放的都没记)。
                    if verdict == "pass" and _app_pkg and recorded and not replayed_recipe and not _nav_path:
                        try:
                            await app_nav.save_step_recipe(_app_pkg, _step_sig, recorded)
                        except Exception:
                            pass
                    break

                step_toast_text = " ".join([s_action, s_expected] + [str(c) for c in s_checks])
                toast_capture = _should_capture_toast(a, act, step_toast_text) or (
                    "点击" in s_action and _should_capture_toast("tap", act, step_toast_text)
                )
                if toast_capture:
                    await _toast_reset(d)

                try:
                    if photo_upload_step and photo_picker_active and a == "back":
                        notes.append(
                            "照片选择器中拦截返回：返回会取消上传；缩略图内容只是待上传照片，不是当前业务页，请继续选图并点完成/确定/添加"
                        )
                        await asyncio.sleep(0.6)
                        continue
                    if not notes and "点击" in s_action and step_targets:
                        direct = await _tap_by_text_once()
                        if direct:
                            if toast_capture:
                                direct = await _append_toast_evidence(d, direct)
                            if photo_upload_step and _is_photo_picker_text(direct):
                                photo_picker_active = True
                            confirm = await _confirm_photo_picker_after_selection(direct)
                            if confirm:
                                direct = f"{direct}；{confirm}"
                            recovery = await _recover_from_photo_preview_if_needed(direct)
                            if recovery:
                                direct = f"{direct}；{recovery}"
                            notes.append(direct)
                            await asyncio.sleep(1.0)
                            continue
                        primary = _primary_tap_target(step_targets)
                        if primary:
                            try:
                                locate_user = (
                                    f"目标文案：{primary}\n"
                                    f"请在当前截图中定位这个入口/卡片，返回应点击的中心坐标。"
                                )
                                locate_raw = await provider.text_multi(
                                    _TARGET_TAP_SYSTEM,
                                    locate_user,
                                    [(b64, "image/jpeg")],
                                    800,
                                    reasoning_effort=_reasoning_effort(),
                                )
                                locate_act = _extract_json(locate_raw)
                                if (locate_act.get("action") or "").lower() == "tap":
                                    dx = int(locate_act.get("x", 0) * scale_x)
                                    dy = int(locate_act.get("y", 0) * scale_y)
                                    await asyncio.to_thread(d.click, dx, dy)
                                    desc = f"按视觉定位点击「{primary}」({dx},{dy}) {locate_act.get('reason', '')}".strip()
                                    if toast_capture:
                                        desc = await _append_toast_evidence(d, desc)
                                    if photo_upload_step and _is_photo_picker_text(desc):
                                        photo_picker_active = True
                                    confirm = await _confirm_photo_picker_after_selection(desc)
                                    if confirm:
                                        desc = f"{desc}；{confirm}"
                                    recovery = await _recover_from_photo_preview_if_needed(desc)
                                    if recovery:
                                        desc = f"{desc}；{recovery}"
                                    notes.append(desc)
                                    await asyncio.sleep(1.0)
                                    continue
                            except Exception as e:
                                notes.append(f"视觉定位「{primary}」失败：{e}")
                    if a == "tap":
                        dx, dy = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                        if photo_upload_step and photo_picker_active and _is_photo_thumbnail_selection_text(act_text):
                            old_dx, old_dy = dx, dy
                            dx, dy = _photo_thumbnail_select_point(dx, dy, dev_w, dev_h)
                            notes.append(f"照片选择器中将缩略图主体点击({old_dx},{old_dy})修正到右上角勾选区域({dx},{dy})")
                        _tgt = (act.get("target") or "").strip()[:12]   # AI 明确指定要点的控件文案(优先)
                        _intent = set(_step_text_targets(act.get("reason", "")))
                        if _tgt:
                            # 有明确 target → 以它为准；别再被 reason 里"当前在X页"这类【状态描述名词】带偏
                            # (0240 就是把要点的"新建"被 reason 里的"业务单据"顶掉、反复点业务单据原地打转)
                            _intent = {_tgt}
                        # ── OCR 优先(点确切文案时)：AI 只负责"点哪个文案"，坐标交给 OCR 精准定位——
                        #    用【重新截取的当前截图】OCR(不用可能已过期的旧图：从截图到真正点击之间隔了 AI 决策
                        #    调用，页面可能已变，用旧图会点错)，找到该文字框、取离 AI 坐标最近的那个再点。
                        #    本地零 token；每步限次防拖慢。OCR 在当前屏找不到该文字→退回下面原逻辑。
                        _op = _tgt or (_primary_tap_target(list(_intent)) if _intent else None)
                        if _op and ocr_tries < 10 and app_ocr.available():
                            ocr_tries += 1
                            try:
                                _fresh = await asyncio.to_thread(d.screenshot)  # 当前最新屏
                            except Exception:
                                _fresh = None
                            _pt = await asyncio.to_thread(app_ocr.locate_text, _fresh, _op, (dx, dy)) if _fresh is not None else None
                            if _pt:
                                await asyncio.to_thread(d.click, int(_pt[0]), int(_pt[1]))
                                desc = f"OCR精准点击「{_op}」({_pt[0]},{_pt[1]}) {act.get('reason', '')[:30]}"
                                if toast_capture:
                                    desc = await _append_toast_evidence(d, desc)
                                if photo_upload_step and _is_photo_picker_text(desc):
                                    photo_picker_active = True
                                if photo_upload_step and photo_picker_active and _is_photo_picker_confirm_text(desc):
                                    photo_picker_active = False
                                confirm = await _confirm_photo_picker_after_selection(desc)
                                if confirm:
                                    desc = f"{desc}；{confirm}"
                                recovery = await _recover_from_photo_preview_if_needed(desc)
                                if recovery:
                                    desc = f"{desc}；{recovery}"
                                logger.info("OCR精准点击「%s」(%d,%d)：case=%s 步%d", _op, _pt[0], _pt[1], case_id, i)
                                recorded.append({"a": "tap", "text": _op})
                                last_intent = _intent
                                notes.append(desc)
                                await asyncio.sleep(1.0)
                                continue
                        # 意图重复兜底：模型连续两次想点【同一命名控件】(从它 reason 抽，如"现场处理")，
                        # 说明上一次坐标点空了。不再让它用不靠谱坐标反复试——直接原生按文案点击(点文字即可切)，
                        # 取不到原生节点再专注定位。跟坐标变没变无关，比"连点同处"稳。每个意图只兜一次。
                        _key = frozenset(_intent)
                        if _intent and (_intent & last_intent) and _key not in tried_intents:
                            tried_intents.add(_key)
                            _rec = await _native_or_relocate(b64, list(_intent))
                            if _rec:
                                if toast_capture:
                                    _rec = await _append_toast_evidence(d, _rec)
                                if photo_upload_step and _is_photo_picker_text(_rec):
                                    photo_picker_active = True
                                if photo_upload_step and photo_picker_active and _is_photo_picker_confirm_text(_rec):
                                    photo_picker_active = False
                                confirm = await _confirm_photo_picker_after_selection(_rec)
                                if confirm:
                                    _rec = f"{_rec}；{confirm}"
                                recovery = await _recover_from_photo_preview_if_needed(_rec)
                                if recovery:
                                    _rec = f"{_rec}；{recovery}"
                                logger.info("意图重复[%s]疑坐标点空，兜底命中：case=%s 步%d %s",
                                            "/".join(_intent), case_id, i, _rec)
                                notes.append(_rec + "（连续两次点同一目标疑点空，改兜底）")
                                recorded.append({"a": "tap", "text": _primary_tap_target(list(_intent))})
                                last_intent = set()
                                await asyncio.sleep(1.2)
                                continue
                            logger.info("意图重复[%s]兜底未命中(原生无节点+定位失败)，退回坐标点击：case=%s 步%d",
                                        "/".join(_intent), case_id, i)
                        last_intent = _intent
                        await asyncio.to_thread(d.click, dx, dy)
                        desc = f"点击({dx},{dy}) {act.get('reason', '')}"
                        # 只把【有文案目标】的 tap 记入经验(可按文案重定位)；纯坐标 tap 不记(回放不可靠)
                        _tt = _primary_tap_target(list(_intent)) if _intent else None
                        if _tt:
                            recorded.append({"a": "tap", "text": _tt})
                    elif a == "input":
                        _txt = act.get("text", "")
                        _ix = int(act.get("x", 0) * scale_x) if act.get("x") is not None else None
                        _iy = int(act.get("y", 0) * scale_y) if act.get("y") is not None else None
                        _ok = await _enter_text(d, _txt, dev_w, dev_h, _ix, _iy)
                        _search_input = _should_trigger_search_after_input(s_action, s_expected)
                        if _search_input:
                            # 搜索/筛选输入后才自动触发搜索。表单 textarea（如“不验机备注”）不能回车，
                            # 否则会干扰焦点/输入法，把普通表单填写误当成搜索流程。
                            for _trg in (lambda: d.send_action("search"), lambda: d.shell("input keyevent 66")):
                                try:
                                    await asyncio.to_thread(_trg)
                                except Exception:
                                    pass
                        await asyncio.sleep(0.8)
                        desc = f"输入「{_txt}」" + ("并触发搜索" if _search_input else "") + ("" if _ok else "（疑未进框）")
                        recorded.append({"a": "input", "value": _txt})
                    elif a == "swipe":
                        _sx = int(act.get("x", 0) * scale_x) if act.get("x") is not None else None
                        _sy = int(act.get("y", 0) * scale_y) if act.get("y") is not None else None
                        await asyncio.to_thread(_swipe, d, dev_w, dev_h, act.get("direction", "up"), _sx, _sy)
                        desc = f"滑动({act.get('direction', 'up')}{('@%d,%d' % (_sx, _sy)) if _sx is not None else ''})"
                        # 滑动=位置相关的导航动作，【不记入经验】(盲回放会跑偏)
                        if act.get("direction", "up") in ("up", "down"):
                            _nav_swipes_before += 1  # 记录竖滑次数，供导航缓存回写"到目标需滚几次"
                    elif a == "enter":
                        # 触发搜索：优先键盘 IME 的“搜索”动作，兜底回车键(不同 App/键盘响应不同)
                        try:
                            await asyncio.to_thread(lambda: d.send_action("search"))
                        except Exception:
                            try:
                                await asyncio.to_thread(d.press, "enter")
                            except Exception:
                                pass
                        desc = "回车/触发搜索"
                        recorded.append({"a": "enter"})
                    elif a == "paste":
                        # 粘贴剪贴板(验证“复制”功能)：先点输入框聚焦，再发 PASTE 键(279)
                        _px = int(act.get("x", 0) * scale_x) if act.get("x") is not None else None
                        _py = int(act.get("y", 0) * scale_y) if act.get("y") is not None else None
                        try:
                            if _px is not None and _py is not None:
                                await asyncio.to_thread(d.click, _px, _py)
                                await asyncio.sleep(0.4)
                            await asyncio.to_thread(lambda: d.shell("input keyevent 279"))
                        except Exception:
                            pass
                        desc = "粘贴剪贴板"
                    elif a == "long_press":
                        _lx, _ly = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                        try:
                            await asyncio.to_thread(lambda: d.long_click(_lx, _ly, 0.8))
                        except Exception:
                            pass
                        desc = f"长按({_lx},{_ly}) {act.get('reason', '')}"
                    elif a == "double_tap":
                        _dx2, _dy2 = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                        try:
                            await asyncio.to_thread(lambda: d.double_click(_dx2, _dy2))
                        except Exception:
                            pass
                        desc = f"双击({_dx2},{_dy2})"
                    elif a == "drag":
                        _sx2, _sy2 = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                        _ex2 = int(act.get("x2", act.get("x", 0)) * scale_x)
                        _ey2 = int(act.get("y2", act.get("y", 0)) * scale_y)
                        try:
                            await asyncio.to_thread(lambda: d.drag(_sx2, _sy2, _ex2, _ey2, 0.5))
                        except Exception:
                            pass
                        desc = f"拖拽({_sx2},{_sy2})→({_ex2},{_ey2})"
                    elif a == "clear":
                        # 清空当前输入框：光标移末尾 + 一串退格(自绘框无法 a11y clear)
                        try:
                            await asyncio.to_thread(lambda: d.shell("input keyevent 123 " + "67 " * 40))
                        except Exception:
                            pass
                        desc = "清空输入框"
                    elif a == "back":
                        await asyncio.to_thread(d.press, "back")
                        desc = "返回"
                        # 返回=状态相关，不记入经验
                    elif a == "wait":
                        await asyncio.sleep(1.5)
                        desc = "等待加载"
                    elif a == "upload":
                        # App 端没有"直接喂文件"的通道，上传必须走系统相册。这里做确定性的那一半：
                        # 把符合张数/大小要求的测试照片推进设备相册并刷新媒体库，保证选图时【一定有】
                        # 满足条件的照片(以前相册里没有合适照片，AI 只能在选择器里空转)。
                        # 剩下的选图/确认仍由 AI 走picker，由 photo_picker_active 那套逻辑护航。
                        _n, _kb = upload_fixtures.parse_upload_request(act, f"{s_action} {s_expected}")
                        pushed = await _seed_device_photos(d, _n, _kb)
                        photo_upload_step = True
                        desc = (f"已向相册注入 {pushed} 张测试照片(单张{_kb}KB)，接下来打开上传控件从相册选择"
                                if pushed else "向相册注入测试照片失败，请改用拍摄或检查设备存储权限")
                        if act.get("x") is not None and act.get("y") is not None:
                            await asyncio.to_thread(d.click, int(act["x"] * scale_x), int(act["y"] * scale_y))
                            desc += "；已点击上传控件"
                    else:
                        desc = f"未知动作 {a}"
                    if toast_capture:
                        desc = await _append_toast_evidence(d, desc)
                    if photo_upload_step and _is_photo_picker_text(desc):
                        photo_picker_active = True
                    if photo_upload_step and photo_picker_active and _is_photo_picker_confirm_text(desc):
                        photo_picker_active = False
                    confirm = await _confirm_photo_picker_after_selection(desc)
                    if confirm:
                        desc = f"{desc}；{confirm}"
                    recovery = await _recover_from_photo_preview_if_needed(desc)
                    if recovery:
                        desc = f"{desc}；{recovery}"
                    notes.append(desc)
                    await asyncio.sleep(1.0)
                except Exception as e:
                    notes.append(f"动作异常：{e}")

            if run_error:
                break
            if verdict is None:
                verdict, reason = "blocked", f"{_MAX_ACTIONS_PER_STEP} 步操作内仍无法判定本步骤"
            if shot is None:  # 未走到 judge(超时等)，补一张当前结果图
                try:
                    _, _, _, png2 = _encode(await asyncio.to_thread(d.screenshot))
                    shot = _save(png2)
                except Exception:
                    pass
            ui_trace.append({
                "seq": i, "action": s_action, "expected": s_expected,
                "verdict": verdict, "verdict_cn": _VERDICT_CN.get(verdict, verdict),
                "reason": reason, "note": "；".join(notes)[:300], "shot": shot,
                "checks": checks_result,
            })

        duration_ms = int((time.monotonic() - t0) * 1000)
        final_shot = next((st["shot"] for st in reversed(ui_trace) if st.get("shot")), None)
        checked_points = coverage_evidence.build_checked_points(covered_items, ui_trace)

        if run_error:
            return RunOutcome(status="error", duration_ms=duration_ms, error_message=run_error,
                              failure_type="env_error", screenshot_url=final_shot, ui_trace=ui_trace,
                              checked_points=checked_points or None, apk_install_ok=apk_install_ok,
                              app_launch_by_package_ok=app_launch_by_package_ok)

        non_pass = [st for st in ui_trace if st["verdict"] != "pass"]
        if not non_pass:
            return RunOutcome(status="passed", duration_ms=duration_ms, screenshot_url=final_shot, ui_trace=ui_trace,
                              checked_points=checked_points or None, apk_install_ok=apk_install_ok,
                              app_launch_by_package_ok=app_launch_by_package_ok)

        summary = "；".join(f"步骤{st['seq']}{st['verdict_cn']}：{st['reason']}" for st in non_pass)[:600]
        # 仅"无法验证(blocked)"且无真实不符 → 归为 env_error(非缺陷，提示补数据/环境)，否则 real_defect
        only_blocked = all(st["verdict"] == "blocked" for st in non_pass)
        return RunOutcome(
            status="failed", duration_ms=duration_ms,
            error_message=("存在无法验证的步骤：" if only_blocked else "存在不符合预期的步骤：") + summary,
            failure_type="env_error" if only_blocked else "real_defect",
            screenshot_url=final_shot, ui_trace=ui_trace,
            checked_points=checked_points or None, apk_install_ok=apk_install_ok,
            app_launch_by_package_ok=app_launch_by_package_ok,
        )

    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError


def _first_device_serial() -> str | None:
    from app.services.devices import list_devices
    devs = list_devices().get("devices") or []
    return devs[0]["serial"] if devs else None


def _encode(img) -> tuple[str, int, int, bytes]:
    import base64
    w, h = img.size
    if w > _SEND_W:
        s = _SEND_W / w
        img = img.resize((_SEND_W, int(h * s)))
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    return base64.b64encode(data).decode(), img.size[0], img.size[1], data


async def _await_to(coro, seconds: float):
    """给可能挂死的远程/AI 调用加【单次超时】：Sonic 远程真机截图/app_current、AI 决策偶发不返回，
    不加超时会干等几分钟。超时抛 asyncio.TimeoutError，由调用点快速失败并重试下一轮，实现自愈。"""
    return await asyncio.wait_for(coro, timeout=seconds)


def _should_trigger_search_after_input(action: str = "", expected: str = "") -> bool:
    """区分搜索输入和普通表单输入。

    以前 input 后无条件 send_action("search") + 回车，这是搜索框友好、表单 textarea 灾难。
    """
    text = f"{action or ''} {expected or ''}"
    return any(k in text for k in ("搜索", "查询", "筛选", "过滤", "模糊", "关键字", "关键词"))


async def _enter_text(d, txt: str, w: int = 0, h: int = 0, x=None, y=None) -> bool:
    """把文字输入到搜索框/表单输入框。

    有 AI 给出的有效坐标时，优先点击该坐标聚焦。这覆盖页面中部/底部的表单 textarea，
    例如“不验机备注”。无坐标时再退回原生 EditText、OCR 占位文案和顶部搜索框兜底。
    """
    def _do() -> bool:
        focused_ok = False
        has_xy = (
            isinstance(x, int) and isinstance(y, int)
            and (not w or 0 < x < w)
            and (not h or 0 < y < h)
        )
        # 0) AI 明确给了 input 坐标：先信任它。这是表单输入框/textarea 的关键路径。
        # 旧逻辑只接受顶部 22% 坐标，导致页面中部“不验机备注”永远点不到焦点。
        if has_xy:
            try:
                d.click(x, y)
                focused_ok = True
            except Exception:
                focused_ok = False
        # a) 原生 EditText：点它聚焦(弹键盘、真实焦点)
        if not focused_ok:
            try:
                et = d(className="android.widget.EditText")
                if et.exists:
                    et.click()
                    focused_ok = True
            except Exception:
                pass
        # b) OCR 定位【搜索框占位文案】并点它聚焦——自绘(Flutter)搜索框无 EditText 节点，
        #    但占位文字("搜索xx")能被 OCR 到，点它就能聚焦搜索框(比顶部区盲点准, 且绝不点到卡片)
        if not focused_ok and app_ocr.available():
            try:
                _im = d.screenshot()
                for kw in ("搜索", "请输入", "输入关键"):
                    pt = app_ocr.locate_text(_im, kw)
                    if pt:
                        d.click(int(pt[0]), int(pt[1]))
                        focused_ok = True
                        break
            except Exception:
                pass
        # c) a11y 占位文案兜底
        if not focused_ok:
            for kw in ("搜索", "请输入", "输入关键"):
                try:
                    anchor = d(textContains=kw)
                    if anchor.exists:
                        anchor.click()
                        focused_ok = True
                        break
                except Exception:
                    continue
        # d) 顶部搜索栏固定区兜底(卡片在下方，不会误点)。仅在 AI 没给有效坐标时使用。
        if not focused_ok and not has_xy and w and h:
            try:
                d.click(int(w * 0.5), int(h * 0.12))
            except Exception:
                pass
        time.sleep(0.5)
        # 先【清空】搜索框：自绘(Flutter)框无法用 a11y clear，用按键——光标移末尾 + 一串退格，
        # 避免与旧值拼接成"xxxxxxxx"。空框时退格无副作用。
        try:
            d.shell("input keyevent 123 " + "67 " * 40)  # 123=MOVE_END, 67=DEL(退格)
            time.sleep(0.2)
        except Exception:
            pass
        # ①【优先 send_keys(走 u2 的 FastInputIME)】——它会与输入框建立 IME 连接, 这样输入后
        #   才能用 send_action("search") 触发搜索(该 App 搜索靠键盘搜索/回车动作触发, 而不是实时)。
        #   adb input text 虽也能把字打进去, 但绕过 IME、之后 send_action 无连接可用、触发不了搜索。
        try:
            d.send_keys(txt, clear=False)
            return True
        except Exception:
            pass
        # ② send_keys 不可用时的兜底：真实注入(至少把字打进去)——纯字母数字用 input text；中文用剪贴板粘贴
        import re as _re
        if _re.fullmatch(r"[A-Za-z0-9]+", (txt or "")):
            try:
                d.shell(["input", "text", txt])
                return True
            except Exception:
                pass
        else:
            try:
                d.set_clipboard(txt)
                time.sleep(0.25)
                d.shell("input keyevent 279")
                return True
            except Exception:
                pass
        try:
            o = d(focused=True)
            if o.exists:
                o.set_text(txt)
                return True
        except Exception:
            pass
        return False
    ok = await asyncio.to_thread(_do)
    await asyncio.sleep(0.5)
    try:  # 校验：dump 里能找到刚输入的文字才算真进框（自绘框找不到再用 OCR/兜底结果）
        xml = await asyncio.to_thread(d.dump_hierarchy)
        if txt and txt in xml:
            return True
    except Exception:
        pass
    if txt and app_ocr.available():
        try:
            im = await asyncio.to_thread(d.screenshot)
            pt = await asyncio.to_thread(app_ocr.locate_text, im, txt)
            if pt:
                return True
        except Exception:
            pass
    return ok


def _swipe(d, w, h, direction: str, x=None, y=None):
    # x/y(设备像素)指定滚动位置的中心：滚多列选择器(如时间选择器左侧【日期列】)时给坐标，
    # 竖滑【围绕 y 小幅度滑动】——不再从屏幕 0.3h 起滑：0.3h 常落在弹层(bottom sheet)顶部/把手区，
    # 从那里下拉手势会把弹层【下拉关闭】(选择器消失)。给了 y 就把手势收敛在目标列区域内，避开关闭区。
    # 竖滑还要【避开底部手势区】并【放慢】——否则华为等手势导航机会把"快速上滑"当成回桌面手势，
    # 把 App 踢到桌面(实测每次滚工作台都掉桌面)。故竖滑端点夹在屏幕中间带、放慢当作滚动而非导航fling。
    has_x = isinstance(x, int) and 0 < x < w
    has_y = isinstance(y, int) and 0 < y < h
    # 竖滑没给 x 时用【屏幕中心】——中心一定落在可滚动内容上，页面才真的会滚动。
    # (曾把它移到右侧空白槽 0.92w 想躲卡片点击，但太靠边常滚不动工作台→找不到入口"时好时坏"；
    #  而慢速大幅滑动本就不会被当成点击，误进详情其实是 input 动作造成的，与竖滑无关，故回退中心。)
    cx = x if has_x else w // 2
    cy = y if has_y else int(h * 0.5)
    # 给了 y(多列选择器)→小幅、收敛在目标列；没给 y(整页浏览找入口)→较大幅，快速滚到底部入口
    # (有 OCR 精准点击兜底，即使某入口一屏划过、下一屏 OCR 也能点中，不必怕划过)。
    dv = int(h * 0.15) if has_y else int(h * 0.24)
    dh = int(w * 0.3)
    lo, hi = int(h * 0.14), int(h * 0.74)   # 竖滑端点夹在中间带，远离底部手势条(避免触发回桌面)
    cl = lambda v: max(lo, min(hi, v))
    dur_v, dur_h = 0.6, 0.45           # 放慢：当滚动而非导航fling
    if direction == "up":       # 手指上滑：内容上移
        d.swipe(cx, cl(cy + dv), cx, cl(cy - dv), dur_v)
    elif direction == "down":   # 手指下滑：内容下移(露出更早/更前的项)
        d.swipe(cx, cl(cy - dv), cx, cl(cy + dv), dur_v)
    elif direction == "left":
        d.swipe(min(w - 5, cx + dh), cy, max(5, cx - dh), cy, dur_h)
    else:
        d.swipe(max(5, cx - dh), cy, min(w - 5, cx + dh), cy, dur_h)


def _save_shot(png: bytes | None, execution_id: str, case_id: str, idx: int) -> str | None:
    if not png:
        return None
    try:
        _UPLOADS.mkdir(parents=True, exist_ok=True)
        # 每条用例只保留【最近一次执行】的截图：本次执行存第一张前，先删掉该用例其它(更早)执行的旧图。
        # 按 case_id 清理，只删别的 execution 的，不动本次；批量执行时各用例 case_id 不同、互不影响。
        if idx == 0:
            for _old in _UPLOADS.glob(f"*_{case_id}_*.jpg"):
                if not _old.name.startswith(f"{execution_id}_"):
                    try:
                        _old.unlink()
                    except OSError:
                        pass
        name = f"{execution_id}_{case_id}_{idx}.jpg"
        (_UPLOADS / name).write_bytes(png)
        return f"/api/executions/shots/{name}"
    except OSError:
        return None
