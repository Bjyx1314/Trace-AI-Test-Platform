"""Temporary login subprocess for an external Web automation framework.

The caller passes credentials through environment variables. This script writes
only the requested temporary storageState file.

Required environment variables:
  TL_FRAMEWORK_ROOT  framework root, for example ./frameworks/web
  TL_FLOW            flow label used only in errors
  TL_FLOW_CLASS      login flow class as module.path:ClassName
  TL_BASE_URL        target base URL
  TL_OUT             temporary storageState output path
  TL_USER / TL_PASS  temporary account credentials
  TL_HAS_TENANT      whether tenant_name should be passed to login_and_assert
  TL_TENANT          optional tenant name
"""
from __future__ import annotations

from importlib import import_module
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FRAMEWORK_ROOT = Path(os.environ["TL_FRAMEWORK_ROOT"])
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

FLOW = os.environ.get("TL_FLOW") or "default"
FLOW_CLASS = os.environ.get("TL_FLOW_CLASS") or ""
BASE_URL = os.environ["TL_BASE_URL"]
OUT = os.environ["TL_OUT"]
USER = os.environ["TL_USER"]
PWD = os.environ["TL_PASS"]
HAS_TENANT = (os.environ.get("TL_HAS_TENANT") or "").strip().lower() in {"1", "true", "yes", "y"}
TENANT = os.environ.get("TL_TENANT") or None


def _load_flow_class(path: str):
    """Load a configured login flow class from module.path:ClassName."""
    mod_name, sep, class_name = path.partition(":")
    if not sep or not mod_name or not class_name:
        raise SystemExit("TL_FLOW_CLASS must be configured as module.path:ClassName")
    try:
        mod = import_module(mod_name)
        return getattr(mod, class_name)
    except Exception as exc:
        raise SystemExit(f"Failed to load login flow {FLOW!r}: {exc}") from exc


def _make_flow(page):
    """Return (flow instance, whether tenant is supported)."""
    cls = _load_flow_class(FLOW_CLASS)
    return cls(page, BASE_URL), HAS_TENANT


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
                print("Login failed: still on the login page")
                return 2
            Path(OUT).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=OUT)
        finally:
            browser.close()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
