"""枚举种子数据初始化脚本（幂等，可重复执行）。

用法:
    python -m app.seed_enums

说明（只插不删，幂等且安全）:
  - 已存在条目 (category, key)：更新 parent_key / sort_order（同步分组与排序），不删除
  - 不存在条目：插入（如端的 api）
  - 数据库中存在但不在 SEED_DATA 的条目（如在「枚举管理」页手动添加的）：原样保留，绝不删除
  - UPDATE_LABELS 中的条目额外更新 label（key 不变）
"""
from __future__ import annotations
import asyncio

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import EnumDefinition


# 只更新 label 不新增的条目 (category, key) -> new_label
UPDATE_LABELS: dict[tuple[str, str], str] = {
    ("priority", "P0"): "P0",
    ("priority", "P1"): "P1",
    ("priority", "P2"): "P2",
}

# (category, key, label, parent_key, sort_order)
SEED_DATA: list[tuple[str, str, str, str | None, int]] = [
    # ── 通用示例研发领域 ─────────────────────────────────────────────────────
    ("product_line", "core", "核心平台", None, 1),
    ("product_line", "commerce", "交易系统", None, 2),
    ("product_line", "growth", "用户增长", None, 3),

    # ── 功能模块 ──────────────────────────────────────────────────────────────
    ("module", "users", "用户管理", None, 1),
    ("module", "projects", "项目管理", None, 2),
    ("module", "orders", "订单管理", None, 3),
    ("module", "payments", "支付结算", None, 4),
    ("module", "reports", "报表中心", None, 5),
    ("module", "notifications", "消息通知", None, 6),

    # ── 端（业务系统）── parent_key = 执行口径分组：pc / app / miniprogram / api ──
    # 执行测试时按 parent_key 决定走 PC(Playwright) / App(真机) / 小程序 / 接口(代码仓库)
    ("platform", "web-admin", "管理后台", "pc", 1),
    ("platform", "web-portal", "用户门户", "pc", 2),
    ("platform", "android-app", "Android App", "app", 3),
    ("platform", "ios-app", "iOS App", "app", 4),
    ("platform", "mini-app", "示例小程序", "miniprogram", 5),
    # 注：api 不是「端」，只是用例类型(case_type=api / 接口)。历史曾把 api 塞进 platform 端，
    # 已由迁移 p0d1e2f3a4b5 删除该端枚举并清理存量用例 platforms 里的 'api'，此处不再登记。

    # ── pc端地址：默认 SIT 环境存于本 base_url 组(key=端名, label=URL)。
    #    其它环境(如开发)存于 base_url_<env> 组(如 base_url_dev)，由管理员在「枚举管理→pc端地址」按 端×环境 录入；
    #    执行时按所选 env 取址，缺则回退该端 SIT。见 app/services/environments.py。
    ("base_url", "web-admin", "https://admin.example.test/", None, 1),
    ("base_url", "web-portal", "https://portal.example.test/", None, 2),

    # ── 用例类型 ──────────────────────────────────────────────────────────────
    ("case_type", "ui", "UI", None, 1),
    ("case_type", "api", "接口", None, 2),

    # ── 用例优先级（label 与 key 保持一致）────────────────────────────────────
    ("priority", "P0", "P0", None, 1),
    ("priority", "P1", "P1", None, 2),
    ("priority", "P2", "P2", None, 3),

    # ── 用例分类 ──────────────────────────────────────────────────────────────
    ("category", "功能", "功能", None, 1),
    ("category", "性能", "性能", None, 2),
    ("category", "安全", "安全", None, 3),
    ("category", "兼容性", "兼容性", None, 4),
    ("category", "其他", "其他", None, 5),
    ("category", "UI", "UI", None, 6),

    # ── 缺陷类型 ──────────────────────────────────────────────────────────────
    ("defect_type", "functional", "功能缺陷", None, 1),
    ("defect_type", "ui", "UI缺陷", None, 2),
    ("defect_type", "performance", "性能缺陷", None, 3),
    ("defect_type", "security", "安全缺陷", None, 4),
    ("defect_type", "compatibility", "兼容性缺陷", None, 5),
    ("defect_type", "other", "其他", None, 6),

    # ── 缺陷严重程度（4级体系，替换旧的 P0/P1/P2 + blocker 等）────────────────
    ("severity", "1级-致命", "1级-致命", None, 1),
    ("severity", "2级-严重", "2级-严重", None, 2),
    ("severity", "3级-一般", "3级-一般", None, 3),
    ("severity", "4级-轻微", "4级-轻微", None, 4),

    # ── 需求来源 ──────────────────────────────────────────────────────────────
    ("source", "manual", "手动创建", None, 1),
    ("source", "feishu", "飞书文档", None, 2),
    ("source", "jira", "JIRA", None, 3),
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        # 1. 更新指定条目的 label（key 不变）
        updated = 0
        for (cat, key), new_label in UPDATE_LABELS.items():
            row = (await db.execute(
                select(EnumDefinition).where(
                    EnumDefinition.category == cat,
                    EnumDefinition.key == key,
                )
            )).scalar_one_or_none()
            if row and row.label != new_label:
                row.label = new_label
                updated += 1

        # 2. upsert：有则同步 parent_key/sort_order，无则插入；不在 SEED_DATA 的一律保留（绝不删除）
        inserted = 0
        synced = 0
        for category, key, label, parent_key, sort_order in SEED_DATA:
            existing = (await db.execute(
                select(EnumDefinition).where(
                    EnumDefinition.category == category,
                    EnumDefinition.key == key,
                )
            )).scalar_one_or_none()
            if existing is not None:
                changed = False
                # parent_key(端的执行口径)现已可在「枚举管理」里配置：仅当从未配置(为空)时用种子回填，
                # 已配置的一律尊重用户设置、不覆盖，避免每次部署把 UI 改的分组冲掉。
                if parent_key and not (existing.parent_key or "").strip():
                    existing.parent_key = parent_key
                    changed = True
                if existing.sort_order != sort_order:
                    existing.sort_order = sort_order
                    changed = True
                if changed:
                    synced += 1
                continue
            db.add(EnumDefinition(
                category=category,
                key=key,
                label=label,
                parent_key=parent_key,
                sort_order=sort_order,
            ))
            inserted += 1

        await db.commit()
        print(
            f"枚举种子同步完成（只插不删）：新增 {inserted} 条，"
            f"同步分组/排序 {synced} 条，label 修订 {updated} 条；不在清单的条目均保留"
        )


if __name__ == "__main__":
    asyncio.run(seed())
