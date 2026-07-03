"""执行机入口 —— 独立的 RQ worker 进程（详设第 4 章）。

与后端共享代码库与数据库，但作为独立进程运行，避免浏览器/设备/子进程把
后端 API 进程拖垮。启动：

    cd backend && python worker.py

监听 settings.task_queue_url 上的 "executions" 队列，消费整批执行 job。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redis import Redis
from rq import Queue, Worker

from app.config import settings


def main():
    conn = Redis.from_url(settings.task_queue_url)
    queue = Queue("executions", connection=conn)
    worker = Worker([queue], connection=conn)
    print(f"[worker] listening on 'executions' @ {settings.task_queue_url}")
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
