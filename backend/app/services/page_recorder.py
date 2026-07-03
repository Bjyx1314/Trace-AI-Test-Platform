"""页面结构缓存「人工录入」录制服务（7.3.1 人工录入 / Playwright 录制）。

当自动探索失败时，由测试人员手动录入。本模块不再要求人工填写 DOM 结构 JSON，
而是直接调用本机已安装的 Playwright CLI 的录制功能（`playwright codegen`）：

  1. 录制前由前端选择 PC 端基础地址（base_url），后端以 PC 桌面视口启动 codegen；
  2. 弹出真实浏览器，**当前登录人自主操作**——点击"开始/结束"由其自己掌控，
     录制即从打开浏览器开始、关闭浏览器结束（这是 codegen 的天然交互）；
  3. 用户关闭浏览器后，解析 codegen 生成的脚本，提取访问过的页面 URL 及交互元素，
     转成页面结构缓存条目，交回 router 写入共享缓存。

本模块只负责"启动录制 + 解析脚本 → 结构化数据"，不碰数据库；落库在 router/上层。
依赖系统已安装的 `playwright` CLI（无需后端引入 playwright Python 包）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

# PC 端桌面视口（录制前"选择 PC 端"的落地：以桌面尺寸而非移动端模拟启动浏览器）
PC_VIEWPORT = "1280,800"

# codegen 生成 python 脚本里典型语句的解析正则
_GOTO_RE = re.compile(r"""page\.goto\(\s*["']([^"']+)["']""")
# get_by_role("button", name="新建用户") —— 文案取 name= 的值
_ROLE_NAME_RE = re.compile(r"""get_by_role\(\s*["'][^"']*["']\s*,\s*name\s*=\s*["']([^"']+)["']""")
# get_by_text / get_by_label / get_by_placeholder / get_by_title("文案")
_LOCATOR_RE = re.compile(r"""get_by_(?:text|label|placeholder|title)\(\s*["']([^"']*?)["']""")


def playwright_cli() -> str | None:
    """返回本机可用的 playwright CLI 路径，未安装返回 None。"""
    return shutil.which("playwright")


class RecorderError(RuntimeError):
    """录制过程中的可向用户展示的错误（CLI 不存在、启动失败、用户未录到内容等）。"""


def record_pages(base_url: str, start_path: str | None = None, timeout_sec: int = 1800) -> list[dict]:
    """启动 Playwright 录制并把录制结果解析为页面结构条目列表。

    base_url   ：已配置的 PC 端基础地址，如 https://app.example.test
    start_path ：可选的起始路径，浏览器打开后直接落在该页（仍可自由跳转）
    timeout_sec：录制最长时长兜底（默认 30 分钟），超时强杀子进程

    返回：[{url_pattern_source_url, page_name, regions:[...]}, ...]
    —— 每个用户访问过的页面一条，regions 由录制到的交互元素聚合而成。
    阻塞直到用户关闭录制浏览器；无可用 CLI 或未录到任何页面时抛 RecorderError。
    """
    cli = playwright_cli()
    if not cli:
        raise RecorderError(
            "未检测到本机 Playwright CLI，无法录制。请先安装：npm i -g playwright && playwright install chromium"
        )

    start_url = urljoin(base_url.rstrip("/") + "/", (start_path or "").lstrip("/")) if start_path else base_url

    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / "recorded.py"
        cmd = [
            cli, "codegen",
            "--target", "python",
            "--viewport-size", PC_VIEWPORT,
            "-o", str(out_file),
            start_url,
        ]
        try:
            # 阻塞等待：用户关闭录制窗口（或超时）后 codegen 退出，脚本写入 out_file
            subprocess.run(cmd, timeout=timeout_sec, check=False)
        except subprocess.TimeoutExpired as e:
            raise RecorderError(f"录制超时（超过 {timeout_sec // 60} 分钟未结束），已自动终止") from e
        except FileNotFoundError as e:
            raise RecorderError("Playwright CLI 启动失败，请确认已正确安装") from e

        if not out_file.exists():
            raise RecorderError("未捕获到录制结果，请重新录制（录制时请至少访问一个页面后再关闭浏览器）")

        script = out_file.read_text(encoding="utf-8", errors="ignore")

    return _parse_recorded_script(script, base_url)


def _parse_recorded_script(script: str, base_url: str) -> list[dict]:
    """把 codegen 生成的 python 脚本解析为按访问页面分组的结构条目。

    策略：按出现顺序扫描语句，遇到 page.goto 切换"当前页面"，把后续的交互
    （点击/填写命中的元素文案）归到当前页面的元素列表，最终每个被访问的页面
    产出一条带"录制交互元素"区块的缓存条目。
    """
    lines = script.splitlines()
    pages: list[dict] = []
    current: dict | None = None

    def _flush():
        if current and current["url"]:
            pages.append(current)

    for line in lines:
        goto = _GOTO_RE.search(line)
        if goto:
            _flush()
            url = goto.group(1)
            # 相对地址补全到 base_url（codegen 一般写绝对地址，这里兜底）
            if not urlparse(url).netloc:
                url = urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))
            current = {"url": url, "elements": []}
            continue
        if current is None:
            continue
        label = _ROLE_NAME_RE.search(line) or _LOCATOR_RE.search(line)
        if label and label.group(1).strip():
            text = label.group(1).strip()
            if ".fill(" in line:
                el_type = "input"
            elif "get_by_role(" in line and ("button" in line.lower() or ".click()" in line):
                el_type = "button"
            else:
                el_type = "element"
            # 去重：同一页面内相同文案+类型只记一次
            sig = (text, el_type)
            if sig not in {(e["name"], e["type"]) for e in current["elements"]}:
                current["elements"].append({
                    "name": text,
                    "selector": _guess_selector(line, text),
                    "type": el_type,
                })

    _flush()

    if not pages:
        raise RecorderError("未从录制中解析到任何页面访问（page.goto），请重新录制")

    return [_to_cache_entry(p) for p in pages]


def _guess_selector(line: str, text: str) -> str:
    """从 codegen 语句里尽量还原一个可读的定位描述，失败则回退到文案匹配。"""
    role = re.search(r"""get_by_role\(\s*["']([^"']+)["']""", line)
    if role:
        return f'role={role.group(1)}[name="{text}"]'  # text 已取自 name= 的值
    if ".get_by_text(" in line:
        return f'text="{text}"'
    if ".get_by_label(" in line:
        return f'label="{text}"'
    if ".get_by_placeholder(" in line:
        return f'placeholder="{text}"'
    return f'text="{text}"'


def _to_cache_entry(page: dict) -> dict:
    """把单页录制结果转成 router 可直接落库的结构条目。"""
    path = urlparse(page["url"]).path or "/"
    page_name = path.strip("/").split("/")[-1] or path
    elements = page["elements"] or []
    regions = [{
        "name": "录制交互区",
        "selector": "body",
        "elements": elements,
    }] if elements else [{
        "name": "页面主体",
        "selector": ".page-container, .ant-layout-content",
        "elements": [],
    }]
    return {
        "source_url": page["url"],
        "page_name": page_name,
        "regions": regions,
    }
