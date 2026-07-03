"""把已存在用户的账号(username)统一回填为姓名拼音(如 张三→zhangsan)。

幂等：已是拼音的跳过；本地账号(auth_source=local，如内置 admin)不动；
重名拼音按 created_at 先到先得，后者追加序号(zhangsan2…)。
在 entrypoint 每次启动时跑一次(安全幂等)。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import PlatformUser
from app.services.pinyin_util import name_to_pinyin


async def _run() -> None:
    async with AsyncSessionLocal() as db:
        users = (await db.execute(
            select(PlatformUser).order_by(PlatformUser.created_at)
        )).scalars().all()
        taken = {u.username for u in users if u.username}
        changed = 0
        for u in users:
            if u.auth_source == "local":
                continue  # 本地账号(admin 等)账号是人为设定，不覆盖
            base = name_to_pinyin(u.name)
            if not base or u.username == base:
                continue
            taken.discard(u.username)  # 释放自己旧账号
            cand, i = base, 1
            while cand in taken:
                i += 1
                cand = f"{base}{i}"
            u.username = cand
            taken.add(cand)
            changed += 1
        if changed:
            await db.commit()
        print(f"[backfill_usernames] 账号回填为拼音：更新 {changed} 个")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
