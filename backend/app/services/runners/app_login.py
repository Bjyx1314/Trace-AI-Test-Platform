"""App 执行前自动登录（AI 视觉驱动，复用 android_runner 的 u2 + 视觉动作协议）。

背景：AndroidAgentRunner 只跑用例步骤，默认 app 已登录。本模块在「装包之后、跑用例之前」
在同一台设备上把 app 登进去，登录态留在设备的 app 里，后续用例复用。

- 登录方式固定：手机号 + 固定验证码（SIT 环境固定码 768235）。
- 每个 app 的「选环境」入口不同 → 用每端一份「配方(goals)」描述入口操作，其余交给 AI 视觉。
- 只有Android App 需要选/切租户（登录后校验左上角租户名，不符则进选择租户页切换）。
- 验证码/账号/环境/期望租户由执行弹框下发（account 每次手输，env 来自枚举，code 固定）。

动作协议与 android_runner 一致：AI 每轮看截图输出 JSON——
  {action: tap,x,y} | {action: input,text} | {action: swipe,direction} | {action: back}
  | {action: wait} | {action: done} 达成本 goal | {action: fail, reason} 无法达成。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

FIXED_SMS_CODE = "768235"          # SIT 默认验证码（未从执行弹框下发 code 时的兜底；非 SIT 环境需在弹框手填对应码）
# 登录失败的确定性信号：这些文案出现即说明账号/验证码不对，立刻停手，别再反复点登录耗到超时。
_LOGIN_ERR_TEXTS = (
    "用户或验证码错误", "账号或验证码错误", "验证码错误", "验证码不正确", "验证码有误",
    "账号或密码错误", "用户名或密码错误", "手机号或验证码错误", "登录失败",
)
_MAX_ACTIONS_PER_GOAL = 14         # 单个 goal 的操作上限（防 AI 无限循环）
_STUCK_LIMIT = 3                   # 连续 N 张相同截图视为卡住
# 动作决策给推理模型的输出预算：含推理 token，给太小(如原来的 400)会被推理吃光→动作 JSON 为空→卡住。
_ACTION_MAX_TOKENS = 3000


def _reasoning_effort() -> str | None:
    """视觉动作循环用低推理档：省 token 且对"看图输出点击"更稳。留空则不下发该参数。"""
    try:
        from app.config import settings
        return (settings.vision_reasoning_effort or "").strip() or None
    except Exception:
        return "low"


# 原生弹窗按钮文案：权限授权/引导/更新弹窗——这些是**原生**控件(在无障碍树里可见)，
# 用 u2 直接按文案点掉，0 token、确定性；避免让视觉模型花调用去处理它们。
# 只收清晰无歧义的词，避开裸"确定/取消/好"(可能误关需要的对话框)。
_POPUP_TEXTS = (
    "允许", "始终允许", "仅在使用中允许", "使用App时允许", "仅使用期间允许", "本次运行时允许",
    "同意并继续", "同意", "允许并继续",
    "跳过", "下一步", "我知道了", "知道了", "开始体验", "立即体验",
    "以后再说", "暂不更新", "稍后再说", "稍后",
)

_SYSTEM = (
    "你是安卓 App 自动登录助手。目标是「把 App 登录到指定环境/账号/租户」。"
    "每轮我给你【当前子目标】和【当前屏幕截图】，你只输出一个 JSON 决定下一步，不要解释：\n"
    "- 需要点击：{\"action\":\"tap\",\"x\":<截图像素x>,\"y\":<截图像素y>,\"reason\":\"\"}\n"
    "- 需要输入文字(先确保已点中对应输入框)：{\"action\":\"input\",\"text\":\"...\"}\n"
    "- 需要滑动浏览：{\"action\":\"swipe\",\"direction\":\"up|down|left|right\"}\n"
    "- 返回上一页：{\"action\":\"back\"}\n"
    "- 等待加载：{\"action\":\"wait\"}\n"
    "- 本子目标已达成：{\"action\":\"done\"}\n"
    "- 本子目标无法达成(找不到入口/异常)：{\"action\":\"fail\",\"reason\":\"...\"}\n"
    "坐标以我给出的截图像素为准。一次只做一个最小动作。子目标达成就立刻 done，不要多做。"
)

# ── 环境(label) → 期望接口 host。选/复核环境时据此二次校验，防止选错环境。 ──
_ENV_HOST: dict[str, str] = {
    "开发环境": "http://api-dev.example.test",
    "平台化开发环境": "http://api-dev.example.test",
    "2.0测试环境": "http://api-sit.example.test",
    "平台化测试环境": "http://api-sit.example.test",
    "业务测试环境": "http://api-sit.example.test",
    "业务开发环境": "http://api-dev.example.test",
}


# ── 配方 → goals 序列（配置驱动）。__RESTART__ 为原生动作：杀 app 重启。 ─────────
# 配方来自 DB(app_login_recipes)：各 App 只在「选环境步骤 / 要不要选租户」上不同，
# 其余(趟启动页、填手机号+固定验证码、勾协议、登录、切租户)都是所有端通用的模板。

# 通用前置目标：把装包后的启动页/引导页/权限弹窗全趟掉，直到登录页。用户新增 App 无需描述这些。
_PRE_LOGIN_GOAL = (
    "检查当前屏幕：若出现开屏广告/启动页/新手引导(『下一步』『跳过』)/"
    "系统或应用的权限授权弹窗(点『允许』『始终允许』『使用App时允许』；**新装的 App 可能连续弹出多个权限弹窗，逐个都点允许**)/"
    "隐私协议弹窗(『同意』『同意并继续』)/版本更新弹窗(『暂不更新』『以后再说』『取消』)等**非登录界面**元素，"
    "点掉或跳过它们；重复直到看到登录页(能看到手机号输入框或『登录』按钮)。已在登录页则直接 done。"
    "此阶段不要输入任何账号或验证码。"
)


def _login_form_goals(phone: str, code: str) -> list[str]:
    return [
        f"当前在登录页。若页面有『账号登录/验证码登录』切换，选择『验证码登录』。"
        f"点中手机号输入框，输入手机号 {phone}。",
        f"点『获取验证码』按钮；界面出现倒计时(如『60s』『重新获取』)后，在验证码输入框填入 {code}。"
        f"若点了获取验证码后一两次仍没看到倒计时，也直接把 {code} 填入验证码框即可(测试环境固定码)，填好就 done。",
        "若登录页有『已阅读并同意』用户协议的勾选框且尚未勾选，点它最前面的圆圈/方框把它勾上。",
        "找到并点击『登录』按钮提交登录，**点击后立即 done**(不用等页面跳转/加载)。",
    ]


def _env_select_goal(env: str) -> str:
    """通用「在环境列表里选中目标环境并确认」。配方只需把 AI 带到环境设置页，选哪个交给它。
    措辞做成安全幂等：已是目标环境 / 本页没有环境可选，都直接 done，避免误操作。"""
    return (
        f"若当前是环境设置/环境切换页(有多个环境可选)：上下滑动浏览，找到并点击选中「{env}」，"
        f"再点『确定/保存/应用/切换』(若有)完成切换后 done。"
        f"若当前环境显示已是「{env}」，或本页没有环境可选项，直接 done，不要乱点。"
    )


def _env_verify_goal(env: str) -> str:
    """重启后快速核一眼当前环境 + host 二次校验；宽松非阻塞：看清才判，看不清不卡不 fail。"""
    host = _ENV_HOST.get(env)
    host_hint = (
        f"该环境对应的接口地址/域名(host)应为「{host}」，可据此确认选对；"
        f"若页面上能看清 host 且与「{host}」明显不符(说明选错了环境)，就重新选中「{env}」并确定后 done。"
        if host else ""
    )
    return (
        f"快速瞥一眼当前环境设置页：若能看出当前选中/生效的环境是「{env}」，直接 done。"
        f"{host_hint}"
        f"若明显是**别的**环境，才重新选中「{env}」并点确定后 done。"
        f"若一两步内看不清、页面没有明显环境标识，也**直接 done**，不要反复找、不要 fail——这只是复核，不是硬性关卡。"
    )


def _back_to_login_goal() -> str:
    """校验完环境后退出设置页，回到登录页准备登录。"""
    return (
        "点返回/关闭图标或系统返回，退出环境设置页；若中途又弹权限/引导/更新弹窗一并点掉，"
        "直到回到登录页(能看到手机号输入框或『登录』按钮)后 done。此阶段不要输入账号或验证码。"
    )


def _tenant_goal(tenant: str, tenant_steps: str | None = None) -> str:
    # 配方给了自定义切租户步骤(如Android App：我的→租户列表)→按它来；否则默认「首页左上角租户名」流程(Android App)。
    if (tenant_steps or "").strip():
        body = tenant_steps.replace("{tenant}", tenant)
        return (
            f"确认/切换当前租户(租户)为【包含「{tenant}」】。按以下方式操作：{body} "
            f"租户匹配用【包含匹配】：只要名称里包含「{tenant}」即视为正确、无需再切。"
            f"完成且当前租户名称包含「{tenant}」后再 done；若翻遍列表都没有包含「{tenant}」的租户，也 done 并说明。"
        )
    return (
        f"登录已完成。查看首页【左上角】显示的租户/租户名称。"
        f"租户匹配用【模糊/包含匹配】：只要名称里包含关键词「{tenant}」(或与之高度近似)即视为匹配，不必完全一致。"
        f"若当前租户已匹配则直接 done；若不匹配，点击左上角租户名称进入「选择租户」页面，"
        f"上下滑动浏览整个列表，找到【名称包含「{tenant}」】的那一项并点击，"
        f"返回首页且左上角租户名称包含「{tenant}」后再 done。"
    )


def _build_goals(recipe: Any, env: str, phone: str, code: str, tenant: str | None) -> list[tuple[str, bool]]:
    """按配方拼装 goals，每项 (子目标, soft)。soft=True 表示尽力而为、失败不阻断登录(如复核/选租户)。

    流程：趟启动页 → 进环境入口 → 选环境 → 重启 → 趟启动页 → [复核环境] → 回登录页 → 登录表单 → 选租户。
    重启后的「重进环境设置 + 校验环境」都是软步骤(复核性质)，失败就跳过；靠随后的「回登录页」硬门确保能登录。
    """
    goals: list[tuple[str, bool]] = [(_PRE_LOGIN_GOAL, False)]
    env_steps = [s.strip() for s in (recipe.env_steps or "").splitlines() if s.strip()]
    for s in env_steps:                                   # 进环境设置入口：必须
        goals.append((s.replace("{env}", env or ""), False))
    if env_steps:                                         # 选中环境+确认：软(幂等)
        goals.append((_env_select_goal(env or ""), True))
    if recipe.restart_after_env and env_steps:
        goals.append(("__RESTART__", False))
        goals.append((_PRE_LOGIN_GOAL, False))            # 重启后趟启动页/连续权限弹窗：必须
        for s in env_steps:                               # 重进环境设置复核：软
            goals.append((s.replace("{env}", env or ""), True))
        goals.append((_env_verify_goal(env or ""), True)) # 复核环境(宽松)：软
        goals.append((_back_to_login_goal(), False))      # 回登录页：硬门(登录表单前保证在登录页)
    form = _login_form_goals(phone, code)
    for g in form[:-1]:                                    # 填号/取码填码/勾协议：必须
        goals.append((g, False))
    goals.append((form[-1], True))                        # 点登录：软——成败以"是否到首页"为准，不因未及时done而误判
    if recipe.needs_tenant and tenant:
        goals.append((_tenant_goal(tenant, getattr(recipe, "tenant_steps", None)), False))  # 选租户：硬门
    return goals


async def _ensure_tenant(d, dev_w, dev_h, provider, recipe: Any,
                         tenant: str | None, phone: str, code: str) -> tuple[bool, str]:
    """租户类 App 必须确认已切到期望租户；未配置租户或该端不需要租户时直接通过。"""
    if not getattr(recipe, "needs_tenant", False) or not tenant:
        return True, ""
    notes: list[str] = []
    ok, reason = await _run_goal(
        d, dev_w, dev_h, provider, _tenant_goal(tenant, getattr(recipe, "tenant_steps", None)),
        notes, phone, code, [], max_actions=_MAX_ACTIONS_PER_GOAL,
    )
    if ok:
        return True, ""
    return False, reason or f"未能切换到期望租户「{tenant}」"


async def _load_recipe(platform_key: str, label: str) -> Any:
    """按 match_keywords 全命中匹配加载启用中的配方(对 "{platform_key} {label}" 小写匹配)。"""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import AppLoginRecipe

    combined = f"{platform_key} {label}".lower()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AppLoginRecipe).where(AppLoginRecipe.enabled.is_(True))
        )).scalars().all()
    for r in rows:
        kws = [k.strip().lower() for k in (r.match_keywords or "").split(",") if k.strip()]
        if kws and all(k in combined for k in kws):
            return r
    return None


async def supports(platform_key: str, label: str) -> bool:
    return (await _load_recipe(platform_key, label)) is not None


async def needs_tenant(platform_key: str, label: str) -> bool:
    """该端的登录配方是否需要选/切租户(供执行弹框决定要不要显示『期望租户』框)。"""
    r = await _load_recipe(platform_key, label)
    return bool(r and getattr(r, "needs_tenant", False))


def _dismiss_native_popups(d) -> bool:
    """确定性关掉一个原生弹窗(权限/引导/更新)——0 token。点掉一个返回 True，无则 False。
    只处理原生控件(在无障碍树里)；Flutter 应用内部弹窗仍交给视觉。

    性能：远程真机(Sonic)上每次 d(...).exists 都是一次慢速 UI 树往返，逐个关键字探测
    (~19 词×2 选择器) 会累积成几分钟的空等。改为【先一次性 dump 整棵树】，只对确实出现在
    XML 里的关键字再做定位点击——无弹窗时(最常见)只花 1 次 dump，而不是数十次远程往返。"""
    try:
        xml = d.dump_hierarchy()
    except Exception:
        xml = ""
    if not xml:
        return False
    for kw in _POPUP_TEXTS:
        if kw not in xml:
            continue
        for sel in (lambda k=kw: d(text=k), lambda k=kw: d(description=k)):
            try:
                obj = sel()
                if obj.exists:
                    obj.click()
                    return True
            except Exception:
                pass
    return False


def _login_error_present(d) -> str | None:
    """确定性检测登录错误提示(读无障碍树文案，0 token)。命中返回该文案，否则 None。
    同样【一次性 dump 再字符串匹配】，避免远程真机上逐词 .exists 的慢往返。"""
    try:
        xml = d.dump_hierarchy()
    except Exception:
        return None
    for kw in _LOGIN_ERR_TEXTS:
        if kw in xml:
            return kw
    return None


async def _drain_native_popups(d, rounds: int = 4) -> None:
    """连续关掉多个原生弹窗(新装 App 常连弹多个权限框)，直到没有可关或达上限。"""
    for _ in range(rounds):
        if not await asyncio.to_thread(_dismiss_native_popups, d):
            return
        await asyncio.sleep(0.8)


async def _native_finish_sms_login(d, phone: str, code: str) -> bool:
    """登录页兜底：原生方式补全验证码登录，减少视觉代理在首屏打转。"""
    def _try_once() -> bool:
        acted = False
        def _click_left_of_text(kw: str, offset: int = 36) -> bool:
            try:
                obj = d(textContains=kw)
                if not obj.exists:
                    return False
                info = obj.info or {}
                b = info.get("bounds") or {}
                cx = max(int(b.get("left", 0)) - offset, 20)
                cy = int((b.get("top", 0) + b.get("bottom", 0)) / 2)
                if cy <= 0:
                    return False
                d.click(cx, cy)
                return True
            except Exception:
                return False

        try:
            if d(textContains=phone).exists:
                acted = True
        except Exception:
            pass
        # 先请求一次验证码/触发可输入态（SIT 固定码；即使取码失败，后面也会直接填固定码）
        for kw in ("获取验证码", "重新获取"):
            try:
                obj = d(textContains=kw)
                if obj.exists:
                    obj.click()
                    acted = True
                    break
            except Exception:
                continue
        # 验证码输入框常显示占位文案“验证码”；有些机型占位文案不可直接点，尝试点左侧输入区域。
        for kw in ("验证码",):
            try:
                obj = d(textContains=kw)
                if obj.exists:
                    obj.click()
                    d.send_keys(code, True)
                    acted = True
                    break
            except Exception:
                if _click_left_of_text(kw, offset=120):
                    try:
                        d.send_keys(code, True)
                        acted = True
                        break
                    except Exception:
                        pass
        # 协议勾选框：点击协议文案左侧区域
        for kw in ("已阅读并同意", "服务协议", "隐私政策"):
            if _click_left_of_text(kw):
                acted = True
                break
        # 登录
        for kw in ("登录",):
            try:
                obj = d(textContains=kw)
                if obj.exists:
                    obj.click()
                    acted = True
                    break
            except Exception:
                continue
        return acted

    acted = await asyncio.to_thread(_try_once)
    if acted:
        await asyncio.sleep(2.0)
    return acted


_JUDGE_SYSTEM = (
    "你判断安卓 App 当前所处状态。看截图，只回一个词，不要解释：\n"
    "- LOGIN：登录页(有手机号输入框/『获取验证码』/『登录』按钮)\n"
    "- HOME：已登录的 App 首页/工作台(有底部导航、『我的』、业务内容，且没有登录入口)\n"
    "- OTHER：启动页/引导页/权限弹窗/加载中/其它无法确定"
)


async def _judge_state(d, provider) -> str:
    """一次视觉判定当前状态：返回 'HOME' | 'LOGIN' | 'OTHER'。用低推理档，省 token。"""
    from app.services.runners.android_runner import _encode
    try:
        img = await asyncio.to_thread(d.screenshot)
        b64, sw, sh, _ = _encode(img)
        raw = await provider.text_multi(
            _JUDGE_SYSTEM, "当前截图见附图，回一个词：LOGIN / HOME / OTHER。",
            [(b64, "image/jpeg")], _ACTION_MAX_TOKENS, reasoning_effort=_reasoning_effort(),
        )
    except Exception as e:  # noqa: BLE001
        logger.info("登录态判定失败(按未登录处理)：%s", e)
        return "OTHER"
    u = (raw or "").upper()
    if "HOME" in u:
        return "HOME"
    if "LOGIN" in u:
        return "LOGIN"
    return "OTHER"


async def _reached_home(d, provider, tries: int = 3, wait: float = 2.0) -> bool:
    """判是否已到首页，带重试(登录后首页可能要加载一会)；每次重试前顺手关原生弹窗。"""
    for i in range(tries):
        if await _judge_state(d, provider) == "HOME":
            return True
        if i < tries - 1:
            await _drain_native_popups(d, rounds=2)
            await asyncio.sleep(wait)
    return False


def _input_tag(text: str, phone: str, code: str) -> str | None:
    """给 input 动作打标，回放时替换成当次账号/验证码。"""
    if text and text == phone:
        return "phone"
    if text and text == code:
        return "code"
    return None


async def _run_goal(d, dev_w, dev_h, provider, goal: str, notes: list[str],
                    phone: str, code: str, trace: list[dict],
                    max_actions: int = _MAX_ACTIONS_PER_GOAL) -> tuple[bool, str]:
    """驱动单个 goal 直到 AI 判 done/fail 或达上限。执行到的动作按序记入 trace(供回放)。返回 (ok, reason)。"""
    from app.services.runners.android_runner import _encode, _swipe  # 懒加载避免循环导入
    from app.agents.llm import _extract_json

    prev_png, same = None, 0
    step_notes: list[str] = []
    for _ in range(max_actions):
        # 先确定性关原生弹窗(0 token)；关掉了就重来一轮，不浪费一次 AI 调用
        if await asyncio.to_thread(_dismiss_native_popups, d):
            await asyncio.sleep(0.8)
            continue
        try:
            img = await asyncio.to_thread(d.screenshot)
        except Exception as e:  # noqa: BLE001
            return False, f"截图失败：{e}"
        b64, sw, sh, png = _encode(img)
        scale_x, scale_y = dev_w / sw, dev_h / sh
        same = same + 1 if (prev_png is not None and png == prev_png) else 0
        prev_png = png
        if same >= _STUCK_LIMIT:
            return False, "界面连续无变化，疑似卡住"

        user = (
            f"当前子目标：{goal}\n\n本子目标已执行：\n" + ("\n".join(step_notes) or "(无)") +
            f"\n\n当前截图宽{sw}高{sh}像素。输出下一步操作 JSON。"
        )
        try:
            raw = await provider.text_multi(_SYSTEM, user, [(b64, "image/jpeg")],
                                            _ACTION_MAX_TOKENS, reasoning_effort=_reasoning_effort())
        except Exception as e:  # noqa: BLE001
            return False, f"AI 决策失败：{e}"
        act = _extract_json(raw)
        a = (act.get("action") or "").lower()

        if a == "done":
            return True, ""
        if a == "fail":
            return False, act.get("reason") or "AI 判定无法达成"
        try:
            if a == "tap":
                dx, dy = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                await asyncio.to_thread(d.click, dx, dy)
                step_notes.append(f"点击({dx},{dy})")
                trace.append({"a": "tap", "x": dx, "y": dy})
            elif a == "input":
                txt = act.get("text", "")
                await asyncio.to_thread(d.send_keys, txt, True)
                step_notes.append(f"输入「{txt}」")
                trace.append({"a": "input", "text": txt, "tag": _input_tag(txt, phone, code)})
            elif a == "swipe":
                dirn = act.get("direction", "up")
                await asyncio.to_thread(_swipe, d, dev_w, dev_h, dirn)
                step_notes.append(f"滑动{dirn}")
                trace.append({"a": "swipe", "dir": dirn})
            elif a == "back":
                await asyncio.to_thread(d.press, "back")
                step_notes.append("返回")
                trace.append({"a": "back"})
            elif a == "wait":
                await asyncio.sleep(1.5)
                trace.append({"a": "wait", "sec": 1.5})
            await asyncio.sleep(1.0)
        except Exception as e:  # noqa: BLE001
            step_notes.append(f"动作异常：{e}")
    notes.extend(step_notes)
    return False, f"{max_actions} 步内未达成子目标"


async def _restart_app(d, pkg: str) -> None:
    logger.info("自动登录执行重启：pkg=%s", pkg)
    await asyncio.to_thread(lambda: d.app_stop(pkg))
    await asyncio.sleep(1)
    # use_monkey=True：用 LAUNCHER 意图启动，兼容"应用包名≠主Activity包名"的 App
    # (如Android App com.example.demo.stj 主Activity 在 com.sostarjob.hatch)，否则 u2 解析主Activity失败起不来。
    await asyncio.to_thread(lambda: d.app_start(pkg, stop=True, use_monkey=True))
    await asyncio.sleep(3)


# ── 登录轨迹缓存(确定性回放) ─────────────────────────────────────────────────
async def _load_script(pkg: str, w: int, h: int) -> list[dict] | None:
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import AppLoginScript
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AppLoginScript).where(
                AppLoginScript.app_package == pkg,
                AppLoginScript.width == w, AppLoginScript.height == h,
            ))).scalar_one_or_none()
        return list(row.script) if row and row.script else None
    except Exception as e:  # noqa: BLE001
        logger.info("读登录轨迹缓存失败(忽略)：%s", e)
        return None


async def _save_script(pkg: str, w: int, h: int, trace: list[dict]) -> None:
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import AppLoginScript
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AppLoginScript).where(
                AppLoginScript.app_package == pkg,
                AppLoginScript.width == w, AppLoginScript.height == h,
            ))).scalar_one_or_none()
            if row:
                row.script = trace
            else:
                db.add(AppLoginScript(app_package=pkg, width=w, height=h, script=trace))
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.info("存登录轨迹缓存失败(忽略)：%s", e)


async def _replay(d, script: list[dict], pkg: str | None, phone: str, code: str) -> None:
    """确定性回放录下的动作序列(坐标按当前分辨率直接用)，中途持续关原生弹窗。"""
    from app.services.runners.android_runner import _swipe
    for step in script:
        await _drain_native_popups(d, rounds=2)
        a = step.get("a")
        try:
            if a == "restart" and pkg:
                await _restart_app(d, pkg)
            elif a == "tap":
                await asyncio.to_thread(d.click, int(step["x"]), int(step["y"]))
            elif a == "input":
                tag = step.get("tag")
                txt = phone if tag == "phone" else code if tag == "code" else step.get("text", "")
                await asyncio.to_thread(d.send_keys, txt, True)
            elif a == "swipe":
                await asyncio.to_thread(_swipe, d, d.window_size()[0], d.window_size()[1], step.get("dir", "up"))
            elif a == "back":
                await asyncio.to_thread(d.press, "back")
            elif a == "wait":
                await asyncio.sleep(float(step.get("sec", 1.5)))
            await asyncio.sleep(1.0)
        except Exception as e:  # noqa: BLE001
            logger.info("回放某步异常(继续)：%s", e)


async def run_login(d, dev_w, dev_h, provider, pkg: str | None, *,
                    platform_key: str, label: str, env: str, phone: str,
                    tenant: str | None = None, code: str | None = None) -> tuple[bool, str, bool]:
    """执行一次自动登录。返回 (ok, message, blocked)。失败不抛，由调用方决定是否继续。

    - ok=True：已登录/登录成功。
    - ok=False, blocked=False：未真正尝试登录(无配方/缺账号/缺环境)，调用方可继续(设备可能已手动登好)。
    - ok=False, blocked=True：确实尝试了但登不进(账号/验证码错误、仍停在登录页)，调用方应中止用例，
      别让用例首步再去登录反复重试耗到超时。

    code 由执行弹框下发(按环境填对应验证码)；未下发时兜底用 SIT 固定码。

    流程：关原生弹窗 → ①已登录则跳过 → ②有缓存轨迹先确定性回放，登进去就收工 →
    ③回放没成/无缓存则走视觉登录并录轨迹 → 视觉登录成功后存缓存，供下次回放。
    """
    recipe = await _load_recipe(platform_key, label)
    if recipe is None:
        return False, f"未找到 {label or platform_key} 的自动登录配方"
    if not phone:
        return False, "缺少账号，跳过自动登录"
    if (recipe.env_steps or "").strip() and not env:
        return False, "该 App 需先选择环境但未提供环境，跳过自动登录"

    code = (code or "").strip() or FIXED_SMS_CODE
    logger.info(
        "开始 App 自动登录：platform=%s label=%s pkg=%s env=%s tenant=%s restart_after_env=%s needs_tenant=%s",
        platform_key, label, pkg, env, tenant,
        getattr(recipe, "restart_after_env", False), getattr(recipe, "needs_tenant", False),
    )
    await _drain_native_popups(d)  # 先把开屏权限弹窗清掉，判定/回放才准

    # ① 已登录也不能直接放过：租户类 App 仍需确认租户正确
    if await _reached_home(d, provider, tries=2, wait=1.5):
        logger.info("自动登录起始态已在 HOME，转入租户校验")
        ok, reason = await _ensure_tenant(d, dev_w, dev_h, provider, recipe, tenant, phone, code)
        if ok:
            return True, "设备上已登录且租户正确，跳过登录流程"
        return False, f"租户不正确：{reason}"

    # ② 有缓存轨迹 → 确定性回放(省 token、稳)。租户型 App 也复用轨迹：轨迹只录到登录完成、不含
    # 选租户动作(见收尾保存),回放后由 _ensure_tenant 动态校正租户——比逼进不稳的视觉登录可靠得多。
    script = await _load_script(pkg or "", dev_w, dev_h)
    if script:
        logger.info("发现登录轨迹缓存(%d 步)，尝试确定性回放", len(script))
        await _replay(d, script, pkg, phone, code)
        await _drain_native_popups(d)
        if await _judge_state(d, provider) == "HOME":
            # 回放已把 App 登进去（且环境是回放里选好的）。此时【只剩租户可能不符】——
            # 就地在首页左上角切租户即可，【绝不重启重登】：重启会清掉登录态、还得重走"选环境"，
            # 而新装包上"选环境"最易失败(找不到入口)，反而把好状态冲坏。切不成就如实报错。
            ok, reason = await _ensure_tenant(d, dev_w, dev_h, provider, recipe, tenant, phone, code)
            if ok:
                return True, "登录轨迹回放成功"
            logger.info("回放已登录但租户未切成，就地再切一次：%s", reason)
            ok, reason = await _ensure_tenant(d, dev_w, dev_h, provider, recipe, tenant, phone, code)
            if ok:
                return True, "回放登录成功，就地切换租户完成"
            return False, f"租户不正确：{reason}"
        logger.info("回放后未登录，回退视觉登录并重录轨迹")
        if pkg:  # 回放确实没登进去(非"仅租户不符")，才冷启动回到干净登录页走视觉
            try:
                await _restart_app(d, pkg)
                await _drain_native_popups(d)
            except Exception:
                pass
            # 重启后 App 的【登录态可能仍在】、直接进了首页(HOME)。此时【绝不能】再按"从登录页开始"的
            # 视觉剧本往下走：那些步骤是登录页专属的(如"点登录页左下角扇形图标进环境设置")，在首页上会
            # 点飞到业务页(实测点成了"新建巡检")，导致整条登录失败、用例直接 error。
            # 已在 HOME 就当已登录：跳过登录与选环境，转租户校验。
            if await _reached_home(d, provider, tries=2, wait=1.5):
                logger.info("回放后重启即在 HOME(登录态仍在)，跳过视觉登录，转租户校验")
                ok, reason = await _ensure_tenant(d, dev_w, dev_h, provider, recipe, tenant, phone, code)
                if ok:
                    return True, "重启后仍在首页(已登录)，跳过登录流程"
                return False, f"租户不正确：{reason}"

    # ③ 视觉登录，逐 goal 执行并录轨迹
    goals = _build_goals(recipe, env, phone, code, tenant)
    notes: list[str] = []
    trace: list[dict] = []
    # 轨迹只保存到「登录完成」为止：选租户是最后一个 goal，其动作是账号相关的坐标点击，
    # 存进轨迹会让回放选到旧租户。记下它开始前的位置，保存时截断，让回放轨迹对租户中立。
    trace_cut: int | None = None
    tenant_gi = len(goals) if (getattr(recipe, "needs_tenant", False) and tenant) else None
    for gi, (goal, soft) in enumerate(goals, start=1):
        if goal == "__RESTART__":
            if pkg:
                try:
                    await _restart_app(d, pkg)
                except Exception as e:  # noqa: BLE001
                    return False, f"重启 App 失败：{e}"
                trace.append({"a": "restart"})
            continue
        if gi == tenant_gi and trace_cut is None:
            trace_cut = len(trace)  # 选租户 goal 之前 → 轨迹截断点
        pre_state = await _judge_state(d, provider)
        logger.info(
            "自动登录 goal %d/%d 开始：soft=%s state=%s goal=%s",
            gi, len(goals), soft, pre_state, goal[:80]
        )
        _cap = 6 if soft else _MAX_ACTIONS_PER_GOAL  # 软步骤(复核/租户)少花几次调用就放行
        ok, reason = await _run_goal(d, dev_w, dev_h, provider, goal, notes, phone, code, trace, _cap)
        post_state = await _judge_state(d, provider)
        logger.info("自动登录 goal %d/%d [%s%s]: %s", gi, len(goals),
                    "OK" if ok else "FAIL", "·soft" if soft else "", goal[:24])
        logger.info(
            "自动登录 goal %d/%d 结束：ok=%s state=%s reason=%s",
            gi, len(goals), ok, post_state, reason or ""
        )
        # 每个 goal 后确定性探一眼登录错误提示：账号/验证码不对就立刻停手，别耗到超时(卡在登录)。
        _err = await asyncio.to_thread(_login_error_present, d)
        if _err:
            return False, f"登录失败：页面提示「{_err}」，验证码({code})可能与所选环境不匹配，请在执行弹框按该环境填写验证码"
        if not ok:
            # 若失败时仍停在验证码登录页，先用原生兜底补一次登录，再重判，避免卡在首屏。
            if post_state == "LOGIN":
                logger.info("自动登录 goal %d 失败时仍在 LOGIN，尝试原生兜底", gi)
                if await _native_finish_sms_login(d, phone, code):
                    if await _reached_home(d, provider, tries=3, wait=2.0):
                        if gi == len(goals):
                            return True, "原生兜底登录成功"
                        continue
            if soft:
                logger.info("软步骤失败，跳过继续：%s", reason)
                continue
            # 硬步骤失败前兜底：若其实已在首页(如设备本就登录着)，直接算成功，别误判失败
            if await _reached_home(d, provider, tries=3, wait=2.5):
                return True, "已在首页(设备已登录)，跳过后续登录步骤"
            return False, f"登录第{gi}步失败：{reason}"

    # ④ 收尾校验：确认真进了首页(多次重试，登录→租户→首页可能要几秒才稳)，才认成功并存轨迹缓存
    if await _reached_home(d, provider, tries=5, wait=2.5):
        logger.info("自动登录收尾校验达到 HOME，开始租户校验")
        ok, reason = await _ensure_tenant(d, dev_w, dev_h, provider, recipe, tenant, phone, code)
        if not ok:
            return False, f"登录成功但租户校验失败：{reason}"
        # 存轨迹时截掉选租户动作(trace_cut)，让回放对租户中立；租户始终由 _ensure_tenant 动态处理
        _to_save = trace if trace_cut is None else trace[:trace_cut]
        if pkg and _to_save:
            await _save_script(pkg, dev_w, dev_h, _to_save)
        return True, "自动登录完成"
    final_state = await _judge_state(d, provider)
    logger.info("自动登录收尾校验未达 HOME：final_state=%s", final_state)
    if final_state == "LOGIN":
        if await _native_finish_sms_login(d, phone, code) and await _reached_home(d, provider, tries=4, wait=2.0):
            ok, reason = await _ensure_tenant(d, dev_w, dev_h, provider, recipe, tenant, phone, code)
            if not ok:
                return False, f"登录成功但租户校验失败：{reason}"
            return True, "原生兜底登录成功"
    _err = await asyncio.to_thread(_login_error_present, d)
    if _err:
        return False, f"登录失败：页面提示「{_err}」，验证码({code})可能与所选环境不匹配，请在执行弹框按该环境填写验证码"
    return False, "登录流程已走完但未确认进入首页"
