"""临时账号登录子进程 —— 复用 PC 自动化框架的登录流程，把 storageState 存到指定临时文件。

由 app.services.web_login.login_temp 以子进程方式调用。账号密码经环境变量传入(不落盘、不入框架)。
所需环境变量：
  TL_FRAMEWORK_ROOT  框架根目录
  TL_FLOW_CLASS      登录流程类，格式 package.module:ClassName
  TL_FLOW_TENANT     true 表示 login_and_assert 需要 tenant_name
  TL_BASE_URL        被测地址
  TL_OUT             storageState 输出文件路径(临时)
  TL_USER / TL_PASS  临时账号密码
  TL_TENANT          可选租户名
"""
from __future__ import annotations

import os
import sys
import importlib
from pathlib import Path

from playwright.sync_api import sync_playwright

FRAMEWORK_ROOT = Path(os.environ["TL_FRAMEWORK_ROOT"])
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

FLOW_CLASS = os.environ.get("TL_FLOW_CLASS") or ""
FLOW_TENANT = (os.environ.get("TL_FLOW_TENANT") or "false").lower() == "true"
BASE_URL = os.environ["TL_BASE_URL"]
OUT = os.environ["TL_OUT"]
USER = os.environ["TL_USER"]
PWD = os.environ["TL_PASS"]
TENANT = os.environ.get("TL_TENANT") or None


def _make_flow(page):
    """动态加载部署者配置的登录流程类。"""
    if ":" not in FLOW_CLASS:
        raise SystemExit("TL_FLOW_CLASS 必须使用 package.module:ClassName 格式")
    module_name, class_name = FLOW_CLASS.split(":", 1)
    flow_class = getattr(importlib.import_module(module_name), class_name)
    return flow_class(page, BASE_URL), FLOW_TENANT


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            flow, has_tenant = _make_flow(page)
            if has_tenant:
                flow.login_and_assert(username=USER, password=PWD, tenant_name=TENANT)
            else:
                flow.login_and_assert(username=USER, password=PWD)
            if "/login" in page.url.lower():
                print("仍在登录页，临时账号登录失败")
                return 2
            Path(OUT).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=OUT)
        finally:
            browser.close()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
