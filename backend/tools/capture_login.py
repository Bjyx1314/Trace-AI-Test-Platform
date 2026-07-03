"""抓取 PC 端登录态(Playwright storageState)——账号变更时重跑即可。

用法(在 backend 目录):
    python -m tools.capture_login web-admin

流程：
  1. 按端名从枚举(base_url 组)取被测地址，打开「有头」浏览器并跳转；
  2. 你在弹出的浏览器里手动完成登录(账号/验证码都行)，进到首页后回到终端按回车；
  3. 自动把 cookies+localStorage 导出到 login_states/<端名>.json；
  4. 之后平台执行该端的 PC 用例时会自动注入这个登录态，浏览器启动即已登录。

说明：这是「手动登录一次→复用登录态」方案，对验证码/复杂登录最稳，且与账号解耦——账号变了重跑本脚本即可。
若你已有现成的 Playwright 登录脚本，也可在脚本末尾用 context.storage_state(path="login_states/<端名>.json") 直接导出，效果相同。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import EnumDefinition
from app.config import settings


async def _base_url_for(platform: str) -> str | None:
    async with AsyncSessionLocal() as db:
        e = (await db.execute(
            select(EnumDefinition).where(
                EnumDefinition.category == "base_url", EnumDefinition.key == platform
            )
        )).scalar_one_or_none()
        return (e.label or "").strip() if e and e.label else None


async def main(platform: str) -> None:
    base_url = await _base_url_for(platform)
    if not base_url:
        print(f"[x] 枚举 base_url 里没有端「{platform}」的地址。可选端见「枚举管理 → base_url」。")
        sys.exit(1)

    out_dir = Path(settings.web_login_state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{platform}.json"

    from playwright.async_api import async_playwright

    print(f"[*] 端「{platform}」地址：{base_url}")
    print("[*] 正在打开浏览器，请在浏览器里手动登录……")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        await page.goto(base_url, wait_until="domcontentloaded")

        # 终端阻塞等用户登录完成（input 是同步的，放线程里以免卡住事件循环）
        await asyncio.get_event_loop().run_in_executor(
            None, input, "\n>>> 登录完成、进入系统首页后，回到这里按【回车】保存登录态…"
        )
        await context.storage_state(path=str(out_file))
        await browser.close()
    print(f"[√] 登录态已保存：{out_file}")
    print("    平台执行该端 PC 用例时会自动注入，浏览器启动即已登录。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python -m tools.capture_login <端名>   例如：python -m tools.capture_login web-admin")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
