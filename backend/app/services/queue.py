"""任务队列 —— 后端入队，独立执行机（RQ worker）消费（详设第 4 章）。

后端只负责把「整批执行」入队；真正的执行在独立 worker 进程里跑（见 worker.py）。
RQ 的 job 函数必须是模块级可导入的同步函数，故这里用 run_execution_job 同步包装
异步的 run_execution。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from redis import Redis
from rq import Queue

from app.config import settings

_redis: Optional[Redis] = None
_queue: Optional[Queue] = None


def get_queue() -> Queue:
    """惰性创建 RQ 队列（连接 settings.task_queue_url）。"""
    global _redis, _queue
    if _queue is None:
        _redis = Redis.from_url(settings.task_queue_url)
        _queue = Queue("executions", connection=_redis)
    return _queue


def run_execution_job(execution_id: str, case_ids: list[str], run_mode: str = "fresh",
                      account_overrides: dict | None = None, reorder: bool = False,
                      ai_key: str | None = None, target_device: str | None = None,
                      env: str | None = None, package_overrides: dict | None = None):
    """RQ worker 实际执行的同步 job：在 worker 进程内跑整批真实执行。"""
    from app.services.execution_runner import run_execution
    asyncio.run(run_execution(execution_id, case_ids, run_mode, account_overrides, reorder, ai_key, target_device, env, package_overrides))


def enqueue_execution(execution_id: str, case_ids: list[str], run_mode: str = "fresh",
                      account_overrides: dict | None = None, reorder: bool = False,
                      ai_key: str | None = None, target_device: str | None = None,
                      env: str | None = None, package_overrides: dict | None = None):
    """把一次整批执行入队，返回 RQ job。ai_key=发起人 key；target_device=App 指定真机；env=PC 执行环境；package_overrides=App换包。"""
    q = get_queue()
    return q.enqueue(
        run_execution_job,
        execution_id, case_ids, run_mode, account_overrides, reorder, ai_key, target_device, env, package_overrides,
        job_timeout=3600,
        result_ttl=86400,
    )
