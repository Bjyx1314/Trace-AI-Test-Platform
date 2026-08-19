"""硬规则引擎初始集种子（幂等，可重复执行）。方案 6.1。

用法:
    python -m app.seed_quality_rules

说明:
  - 从 code-change-test-impact skill 的 reference.md「Blast Radius Signals」结构化为平台硬规则。
  - 命中逻辑最简：covered_item.risk_tags ∩ match_tags 非空即命中 → 抬优先级 min_priority、
    补 required_covered_items、在用例 matched_rules 留痕（见 services/quality_rule_engine.py）。
  - 幂等：按 id 匹配，已存在则同步字段，不存在则插入；不在清单内的既有规则一律保留。
  - MVP 只读导入，无编辑界面。
"""
from __future__ import annotations
import asyncio

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import QualityRule

_SRC = "code-change-test-impact/reference.md#blast-radius"

# 风险标签用中文，与 AI 打标 risk_tags 口径一致（金额/支付/认证/权限/幂等/迁移…）
SEED: list[dict] = [
    {
        "id": "R-001", "name": "认证/授权/会话/租户隔离变更强制 P0",
        "match_tags": ["认证", "授权", "登录", "鉴权", "会话", "session", "租户", "权限"],
        "min_priority": "P0",
        "required_covered_items": ["未登录访问被拦截", "越权访问被拒绝", "会话过期处理正确"],
        "source": _SRC,
    },
    {
        "id": "R-002", "name": "支付/计费/退款/结算变更强制 P0",
        "match_tags": ["支付", "金额", "计费", "退款", "结算", "价格", "payment", "billing", "refund"],
        "min_priority": "P0",
        "required_covered_items": ["支付金额计算正确", "重复支付幂等", "退款金额正确"],
        "source": _SRC,
    },
    {
        "id": "R-003", "name": "核心域生命周期(订单/库存/履约/合同)变更强制 P1",
        "match_tags": ["订单", "库存", "履约", "合同", "生命周期", "状态流转"],
        "min_priority": "P1",
        "required_covered_items": ["核心状态流转正确", "并发操作数据一致"],
        "source": _SRC,
    },
    {
        "id": "R-004", "name": "共享校验/序列化/全局异常处理变更强制 P1",
        "match_tags": ["公共校验", "共享校验", "序列化", "全局异常", "common", "公共组件"],
        "min_priority": "P1",
        "required_covered_items": ["所有调用方回归正常", "边界输入校验正确"],
        "source": _SRC,
    },
    {
        "id": "R-005", "name": "数据库迁移/表结构变更强制 P1",
        "match_tags": ["数据库迁移", "DDL", "表结构", "migration", "字段删除", "schema"],
        "min_priority": "P1",
        "required_covered_items": ["迁移后 CRUD 正常", "存量数据兼容", "报表查询正确"],
        "source": _SRC,
    },
    {
        "id": "R-006", "name": "MQ 幂等消费变更强制 P1",
        "match_tags": ["MQ", "消息", "幂等", "消费", "kafka", "rocketmq"],
        "min_priority": "P1",
        "required_covered_items": ["重复消息幂等", "消费失败重试正确"],
        "source": _SRC,
    },
    {
        "id": "R-007", "name": "公共 Feign 接口/契约变更强制全消费方回归 P1",
        "match_tags": ["Feign", "接口契约", "api契约", "DTO", "枚举删值", "字段删除"],
        "min_priority": "P1",
        "required_covered_items": ["全部已知消费方回归", "字段兼容性正确"],
        "source": _SRC,
    },
]

_SYNC_FIELDS = ("name", "match_tags", "min_priority", "required_covered_items", "source")


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        inserted = synced = 0
        for item in SEED:
            existing = (await db.execute(
                select(QualityRule).where(QualityRule.id == item["id"])
            )).scalar_one_or_none()
            if existing is not None:
                changed = False
                for f in _SYNC_FIELDS:
                    if getattr(existing, f) != item.get(f):
                        setattr(existing, f, item.get(f))
                        changed = True
                if changed:
                    synced += 1
                continue
            db.add(QualityRule(**item))
            inserted += 1
        await db.commit()
        print(f"硬规则初始集同步完成：新增 {inserted} 条，同步 {synced} 条")


if __name__ == "__main__":
    asyncio.run(seed())
