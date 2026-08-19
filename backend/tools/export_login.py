"""Export a login storageState from an external Web automation config.

This helper reads only generic, user-provided framework metadata. Configure
platform mappings with WEB_LOGIN_EXPORTS_JSON, for example:

{
  "demo-web": {
    "project": "demo",
    "web": "main",
    "role": "default",
    "username_placeholder": "Username",
    "password_placeholder": "Password"
  }
}
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import yaml

from app.config import settings

FRAMEWORK_PROJECTS_YAML = Path(r"./frameworks/web/common/config/projects.yaml")


def _load_platforms() -> dict[str, dict]:
    raw = os.environ.get("WEB_LOGIN_EXPORTS_JSON") or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


PLATFORM_TO_FRAMEWORK: dict[str, dict] = _load_platforms()


def _load_framework_cfg(platform: str) -> tuple[str, str, str, dict]:
    """Return (base_url, username, password, platform config)."""
    item = PLATFORM_TO_FRAMEWORK.get(platform)
    if not item:
        raise SystemExit(
            f"[x] Platform {platform!r} is not configured. "
            "Set WEB_LOGIN_EXPORTS_JSON before running this helper."
        )
    proj_key = item.get("project")
    web_key = item.get("web")
    role = item.get("role") or "default"
    cfg = yaml.safe_load(FRAMEWORK_PROJECTS_YAML.read_text(encoding="utf-8"))
    proj = cfg["projects"][proj_key]
    base_url = proj["base_urls"][web_key]
    acc = proj["auth"]["accounts"][web_key][role]
    return base_url, acc["username"], acc["password"], item


async def main(platform: str) -> None:
    base_url, username, password, item = _load_framework_cfg(platform)
    out_dir = Path(settings.web_login_state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{platform}.json"

    user_placeholder = item.get("username_placeholder") or "Username"
    pass_placeholder = item.get("password_placeholder") or "Password"
    login_text = item.get("login_button_text") or r"log\s*in|sign\s*in"

    from playwright.async_api import async_playwright

    login_url = base_url.rstrip("/") + "/login"
    print(f"[*] Platform {platform!r} login page: {login_url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        await page.goto(login_url, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)
        await page.get_by_placeholder(user_placeholder).fill(username)
        await page.get_by_placeholder(pass_placeholder).fill(password)
        await page.get_by_role("button", name=re.compile(login_text, re.I)).click()
        await asyncio.sleep(3)
        final = page.url
        await context.storage_state(path=str(out_file))
        await browser.close()
    print(f"[OK] Final URL: {final}")
    print(f"[OK] storageState saved: {out_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m tools.export_login <platform>")
    asyncio.run(main(sys.argv[1]))
