"""Agent 3: 脚本生成 — generates automation scripts (接口/web/app) from test cases."""
from __future__ import annotations
import json
from .base_agent import BaseAgent

SYSTEM_TEMPLATES = {
    "api": """你是一名资深接口自动化测试工程师，熟悉 Python httpx + pytest。
根据提供的测试用例，生成可运行的接口自动化测试脚本。
- 使用 httpx.Client 发起请求
- 每个步骤对应一段代码，添加注释
- 包含断言语句
- 只输出 Python 代码，不要 markdown 围栏""",
    "web": """你是一名资深Web UI自动化测试工程师，熟悉 Playwright + pytest。
根据提供的测试用例，生成可运行的Web UI自动化测试脚本。
- 使用 Playwright 的 page fixture 操作页面
- 每个步骤对应一段代码，添加注释
- 包含断言语句
- 只输出 Python 代码，不要 markdown 围栏""",
    "app": """你是一名资深App自动化测试工程师，熟悉 Appium + pytest。
根据提供的测试用例，生成可运行的App UI自动化测试脚本。
- 使用 Appium webdriver fixture 操作App元素
- 每个步骤对应一段代码，添加注释
- 包含断言语句
- 只输出 Python 代码，不要 markdown 围栏""",
    "harmony": """你是一名资深鸿蒙(HarmonyOS NEXT)自动化测试工程师，熟悉 hypium + pytest。
根据提供的测试用例，生成可运行的鸿蒙 UI 自动化测试脚本。
- 使用 hypium 的 driver 操作 ArkUI 组件（BY.id / BY.key / BY.text 定位）
- 每个步骤对应一段代码，添加注释
- 包含断言语句
- 只输出 Python 代码，不要 markdown 围栏""",
    "miniprogram": """你是一名资深微信小程序自动化测试工程师，熟悉 minium + pytest。
根据提供的测试用例，生成可运行的微信小程序自动化测试脚本。
- 使用 minium 的 app/page 操作小程序页面与组件
- 每个步骤对应一段代码，添加注释
- 包含断言语句
- 只输出 Python 代码，不要 markdown 围栏""",
}


# case_type/platforms → 脚本模板类型。与 services.runners.dispatcher 的路由口径保持一致：
# 接口最高优先，其余按 miniprogram>harmony>ios/android(app)>web。
_PLATFORM_TO_SCRIPT = (
    ("miniprogram", "miniprogram"),
    ("harmony", "harmony"),
    ("ios", "app"),
    ("android", "app"),
    ("web", "web"),
)


def determine_script_type(case_type: str, platforms: list[str]) -> str:
    """根据用例类型与适用端选择自动化脚本类型。

    api/web/app(appium)/harmony/miniprogram 之一。接口（case_type==api 或含
    backend_api）走 api；否则按平台优先级匹配；都不命中默认 web。
    """
    platforms = platforms or []
    if case_type == "api" or "backend_api" in platforms:
        return "api"
    for platform, script_type in _PLATFORM_TO_SCRIPT:
        if platform in platforms:
            return script_type
    return "web"


def _mock_script(case: dict, script_type: str) -> str:
    title = case.get("title", "test_case")
    safe = title.replace(" ", "_").replace("-", "_")[:40].lower()
    steps = case.get("steps", [])
    step_lines = "\n".join(f"    # {s.get('step','')}" for s in steps)

    if script_type == "api":
        return f'''import httpx


def test_{safe}():
    """
    {case.get("description", "")}
    Expected: {case.get("expected_result", "")}
    """
    client = httpx.Client(base_url="http://localhost:8000")
{step_lines}
    # Assert
    assert True, "Test passed (mock)"
'''
    if script_type == "app":
        return f'''import pytest
from appium import webdriver


def test_{safe}(driver):
    """
    {case.get("description", "")}
    Expected: {case.get("expected_result", "")}
    """
{step_lines}
    # Assert
    assert True, "Test passed (mock)"
'''
    return f'''import pytest
from playwright.sync_api import Page


def test_{safe}(page: Page):
    """
    {case.get("description", "")}
    Expected: {case.get("expected_result", "")}
    """
{step_lines}
    # Assert
    assert True, "Test passed (mock)"
'''


class ScriptGeneratorAgent(BaseAgent):
    async def generate_script(self, test_case: dict, script_type: str = "web") -> str:
        if self.use_mock:
            return _mock_script(test_case, script_type)

        system = SYSTEM_TEMPLATES.get(script_type, SYSTEM_TEMPLATES["web"])
        prompt = (
            f"测试用例:\n{json.dumps(test_case, ensure_ascii=False, indent=2)}\n\n"
            "请生成对应的自动化测试脚本。"
        )
        return await self.call_claude(system, prompt, max_tokens=2048)
