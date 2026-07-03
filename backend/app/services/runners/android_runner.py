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
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from .base import BaseRunner, RunOutcome, RunContext

logger = logging.getLogger(__name__)

# 同一真机同一时刻只能跑一条 App 用例(一块屏)，用进程内锁串行化，多入口/并发触发时自动排队
_DEVICE_LOCK = asyncio.Lock()

_UPLOADS = Path(__file__).resolve().parents[3] / "uploads" / "exec_shots"
_SEND_W = 540                # 发给 AI 的截图宽度(等比缩放)，AI 坐标需按 scale 还原回设备分辨率
_MAX_ACTIONS_PER_STEP = 16   # 单步操作上限(防 AI 无限循环/烧钱的兜底)
_STUCK_LIMIT = 3             # 连续 N 张截图完全相同视为"卡住无进展"，提前结束本步

_SYSTEM = (
    "你是认真细致的手机 App 自动化测试代理，当前只专注【一个测试步骤】。会给你：该步骤的操作与预期、本步已执行操作、当前手机截图。"
    "像测试员一样，先把界面导航/操作到位、充分核对后，再判定本步骤。严格输出一个 JSON(不要解释/markdown)：\n"
    '{"action":"tap|input|swipe|back|wait|judge","x":数,"y":数,"text":"输入内容",'
    '"direction":"up|down|left|right","verdict":"pass|fail|blocked","reason":"依据或原因",'
    '"checks":[{"point":"锚点原文","ok":true或false}]}\n'
    "规则：\n"
    "- 坐标 x,y 用当前截图像素坐标(左上角0,0)指控件中心；tap点击/input在已聚焦框输入/swipe滑动/back返回/wait等待；遇无关弹窗(如版本更新)先关闭；\n"
    "- 【精确导航·子系统要分清】本步开始时界面可能停留在上一步/首页，先核对当前是否为本步骤的正确页面，不是则【坚持导航】过去(可多次点菜单/展开子菜单/返回换路径，不要点一次进不去就放弃)；"
    "严格按步骤描述进入正确的子系统与入口，特别注意区分名称相近但不同的入口——例如“管理后台”与“用户门户”是不同子系统、“新建订单”与“新建订单模板”是不同入口，必须进入与描述完全一致的那个；"
    "若发现进错了子系统/页面，用 back 返回重新找正确入口，绝不将就用错的页面去判定；\n"
    "- 【逐项充分验证】当预期是“所有必填项都校验/都提示”这类，要逐个必填项核对：提交→看缺哪个必填项的提示→补一个→再提交，循环直到覆盖所有必填项，不能只验证一个就判 pass；\n"
    "- 【主动探索·必须看全】判定前必须把页面探索完整：向下滚动直到页面底部、横向滚动看完表格/列表的全部列(表格右侧常有更多列被遮住)；"
    "在判断“某字段/某列缺失、不符”之前，务必已经横向滚到表格最右端、纵向滚到底，多次尝试仍找不到才算缺失，不能只看首屏就下结论；"
    "目标控件不在当前屏就滚动/返回/换路径去找，遇到阻碍想办法绕过(不同入口、先建数据再核对)，不要浅尝就放弃；\n"
    "- 【先等数据加载再判断】若列表/表格在加载中、显示骨架屏，或显示“共0条/暂无数据”但很可能只是还没加载完(例如刚进入页面、刚点查询)，"
    "先输出 action=wait 等待，再重新观察后判断；绝不在数据尚未稳定时就下“为空/缺失”的结论；\n"
    "- 【判定锚点】若给了本步骤的判定锚点(check_points)，judge 时必须【逐条】对照当前界面核对，在 checks 里给出每条 {point, ok}；"
    "只有所有【应满足】的锚点都 ok=true 才可 pass；有应满足的锚点 ok=false → 据其性质判 fail(与预期不符)或 blocked(无数据/到不了)，并在 reason 说明；\n"
    "- 当确已完成本步骤验证时输出 action=judge 并给 verdict：\n"
    "  pass=锚点全部满足且符合预期；"
    "fail=【确已进入目标页面】但功能/数据/字段与预期不符(真正的产品缺陷)；"
    "blocked=无法验证——如没能进入/找不到目标页面、进错子系统未能纠正、目标页无数据、反复尝试仍到不了。\n"
    "【关键】‘没进对页面/没找到入口’属于 blocked(无法验证)，绝不能判 fail——fail 只用于真的进到了正确页面、看清内容后确认不符；不确定是否进对页面时判 blocked。\n"
    "【重要纪律】没有把预期要求的内容/锚点都核对完之前不要判 pass；没有任何可核对内容时才判 blocked；绝不臆断。"
)

_VERDICT_CN = {"pass": "通过", "fail": "不符(缺陷)", "blocked": "无法验证"}


class AndroidAgentRunner(BaseRunner):
    platform = "android"
    requires_device = True

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        # 同一时刻只允许一条 App 用例驱动真机，其余排队等待(设备只有一块屏)
        async with _DEVICE_LOCK:
            return await self._drive(case, ctx)

    async def _drive(self, case: Any, ctx: RunContext) -> RunOutcome:
        t0 = time.monotonic()
        try:
            import uiautomator2 as u2
        except Exception as e:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"未安装 uiautomator2，无法直连真机执行：{e}", failure_type="env_error")

        serial = ctx.device_udid or _first_device_serial()
        if not serial:
            return RunOutcome(status="error", duration_ms=0,
                              error_message="未检测到已连接的安卓真机(adb devices 为空)", failure_type="env_error")
        try:
            # 加超时：uiautomator2 首次连机要往手机推/起服务，卡住时别无限等(常见「卡在 adb」)
            d = await asyncio.wait_for(asyncio.to_thread(u2.connect, serial), timeout=90)
            dev_w, dev_h = await asyncio.wait_for(asyncio.to_thread(d.window_size), timeout=30)
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
                ok, msg = await asyncio.to_thread(
                    install_apk, serial, _apk.get("source"), _apk.get("package")
                )
                if not ok:
                    return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                      error_message=f"更换测试包失败：{msg}")

            # 直接启动目标 App（枚举「端→应用包名」配置）：让 AI 一上来就在正确的 App 里，
            # 不用在手机桌面视觉找图标（避免找错 App、提速）。启动失败不阻断，退回原有 AI 流程。
            _app_pkg = (ctx.extra or {}).get("app_package") if isinstance(ctx.extra, dict) else None
            if _app_pkg:
                try:
                    await asyncio.to_thread(lambda: d.app_start(_app_pkg, stop=True))
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.info("按包名启动 App(%s) 失败，退回 AI 桌面查找：%s", _app_pkg, e)
        except asyncio.TimeoutError:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"连接真机超时({serial})：uiautomator2 初始化卡住，请检查手机USB调试授权/数据线，或手机上是否弹出安装确认", failure_type="env_error")
        except Exception as e:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"连接真机失败({serial})：{e}", failure_type="env_error")

        steps = getattr(case, "steps", None) or [
            {"action": getattr(case, "title", "执行用例"), "expected": getattr(case, "expected_result", "") or ""}
        ]
        title = getattr(case, "title", "")
        case_id = getattr(case, "id", "case")

        from app.agents.llm import get_provider, _extract_json
        provider = get_provider()
        shot_i = 0

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
            s_checks = step.get("check_points") or []     # 该步判定锚点
            checks_result: list[dict] = []                # AI 逐条核对结果
            shot: str | None = None      # 本步骤一张结果截图(判定时刻)
            notes: list[str] = []
            verdict, reason = None, ""
            prev_png, same_count = None, 0   # 卡死检测：连续相同截图计数

            for _ in range(_MAX_ACTIONS_PER_STEP):
                try:
                    img = await asyncio.to_thread(d.screenshot)
                except Exception as e:
                    run_error = f"截图失败：{e}"
                    break
                b64, sw, sh, png = _encode(img)
                scale_x, scale_y = dev_w / sw, dev_h / sh

                # 卡死兜底：连续多张画面完全相同且非主动等待，判为无进展，提前结束本步
                same_count = same_count + 1 if (prev_png is not None and png == prev_png) else 0
                prev_png = png
                if same_count >= _STUCK_LIMIT:
                    verdict = verdict or "blocked"
                    reason = reason or "界面连续无变化，疑似卡住，无法继续操作以完成本步骤"
                    shot = _save(png)
                    break

                checks_text = ("\n判定锚点(逐条核对)：\n" + "\n".join(f"- {p}" for p in s_checks)) if s_checks else ""
                user = (
                    f"测试用例：{title}\n\n"
                    f"当前步骤 {i}/{len(steps)}：\n操作：{s_action}\n预期：{s_expected}{checks_text}\n\n"
                    f"本步已执行：\n" + ("\n".join(notes) or "(无)") +
                    f"\n\n当前截图宽{sw}高{sh}像素。请输出下一步操作 JSON，或在可判定时输出 judge+verdict"
                    + ("(并在 checks 里逐条给出锚点核对结果)" if s_checks else "") + "。"
                )
                try:
                    raw = await provider.text_multi(_SYSTEM, user, [(b64, "image/jpeg")], 600)
                except Exception as e:
                    run_error = f"AI 决策失败：{e}"
                    break
                act = _extract_json(raw)
                a = (act.get("action") or "").lower()

                if a == "judge":
                    verdict = act.get("verdict") if act.get("verdict") in ("pass", "fail", "blocked") else "blocked"
                    reason = act.get("reason") or ""
                    raw_checks = act.get("checks") if isinstance(act.get("checks"), list) else []
                    checks_result = [
                        {"point": str(c.get("point", "")), "ok": bool(c.get("ok"))}
                        for c in raw_checks if isinstance(c, dict)
                    ]
                    # 兜底纪律：判 pass 但有锚点未满足 → 降级为 blocked，避免假通过
                    if verdict == "pass" and checks_result and any(not c["ok"] for c in checks_result):
                        miss = "、".join(c["point"] for c in checks_result if not c["ok"])
                        verdict = "blocked"
                        reason = (reason + f"；但锚点未满足：{miss}").strip("；")
                    shot = _save(png)  # 本步结果截图
                    break

                try:
                    if a == "tap":
                        dx, dy = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                        await asyncio.to_thread(d.click, dx, dy)
                        desc = f"点击({dx},{dy}) {act.get('reason', '')}"
                    elif a == "input":
                        await asyncio.to_thread(d.send_keys, act.get("text", ""), True)
                        desc = f"输入「{act.get('text', '')}」"
                    elif a == "swipe":
                        await asyncio.to_thread(_swipe, d, dev_w, dev_h, act.get("direction", "up"))
                        desc = f"滑动({act.get('direction', 'up')})"
                    elif a == "back":
                        await asyncio.to_thread(d.press, "back")
                        desc = "返回"
                    elif a == "wait":
                        await asyncio.sleep(1.5)
                        desc = "等待加载"
                    else:
                        desc = f"未知动作 {a}"
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

        if run_error:
            return RunOutcome(status="error", duration_ms=duration_ms, error_message=run_error,
                              failure_type="env_error", screenshot_url=final_shot, ui_trace=ui_trace)

        non_pass = [st for st in ui_trace if st["verdict"] != "pass"]
        if not non_pass:
            return RunOutcome(status="passed", duration_ms=duration_ms, screenshot_url=final_shot, ui_trace=ui_trace)

        summary = "；".join(f"步骤{st['seq']}{st['verdict_cn']}：{st['reason']}" for st in non_pass)[:600]
        # 仅"无法验证(blocked)"且无真实不符 → 归为 env_error(非缺陷，提示补数据/环境)，否则 real_defect
        only_blocked = all(st["verdict"] == "blocked" for st in non_pass)
        return RunOutcome(
            status="failed", duration_ms=duration_ms,
            error_message=("存在无法验证的步骤：" if only_blocked else "存在不符合预期的步骤：") + summary,
            failure_type="env_error" if only_blocked else "real_defect",
            screenshot_url=final_shot, ui_trace=ui_trace,
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


def _swipe(d, w, h, direction: str):
    cx, cy = w // 2, h // 2
    if direction == "up":
        d.swipe(cx, int(h * 0.7), cx, int(h * 0.3), 0.3)
    elif direction == "down":
        d.swipe(cx, int(h * 0.3), cx, int(h * 0.7), 0.3)
    elif direction == "left":
        d.swipe(int(w * 0.7), cy, int(w * 0.3), cy, 0.3)
    else:
        d.swipe(int(w * 0.3), cy, int(w * 0.7), cy, 0.3)


def _save_shot(png: bytes | None, execution_id: str, case_id: str, idx: int) -> str | None:
    if not png:
        return None
    try:
        _UPLOADS.mkdir(parents=True, exist_ok=True)
        name = f"{execution_id}_{case_id}_{idx}.jpg"
        (_UPLOADS / name).write_bytes(png)
        return f"/api/executions/shots/{name}"
    except OSError:
        return None
