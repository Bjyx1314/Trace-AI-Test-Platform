"""执行控制：进程内「取消标志 + 实时日志环形缓冲」。

App 真机执行走 execution_inproc（在后端进程内跑），因此用进程内内存即可让
「取消执行」信号与「实时日志」在 HTTP 接口和执行协程之间共享，无需额外中间件。

- 取消：cancel 端点把 execution_id 加入 _CANCELED；执行器在每条用例前、android_runner 在每个
  动作前检查 is_canceled → 尽快中断（App 用例每个动作约数十秒，逐动作检查即可较快停下）。
- 日志：执行器/Runner 每步 push 一行，前端轮询 get_logs(after=seq) 增量拉取。环形缓冲，只留最近若干行。

注意：后端进程重启会清空（取消标志与日志都在内存）。实时观测场景够用；执行状态本身仍以 DB 为准。
"""
from __future__ import annotations

import time
import threading
from collections import deque, defaultdict

_LOCK = threading.Lock()
_CANCELED: set[str] = set()          # 整条执行(批次)取消：execution_id
_CANCELED_CASES: set[str] = set()    # 单条用例取消：case_id（批量执行时只停这一条，不动其它）
_LOGS: dict[str, deque] = {}
_SEQ: dict[str, int] = defaultdict(int)
_MAX_LINES = 1200   # 批量执行多条用例共用一条执行日志，放大缓冲以容纳多条用例的行


def request_cancel(execution_id: str) -> None:
    with _LOCK:
        _CANCELED.add(execution_id)


def is_canceled(execution_id: str | None) -> bool:
    if not execution_id:
        return False
    return execution_id in _CANCELED


def clear(execution_id: str) -> None:
    """执行真正结束后清理取消标志（日志保留供收尾查看，随环形缓冲自然淘汰）。"""
    with _LOCK:
        _CANCELED.discard(execution_id)


def request_cancel_case(case_id: str) -> None:
    """只取消批次里的某一条用例（点单个用例后面的"取消测试"）：停这条、其余照跑。"""
    with _LOCK:
        _CANCELED_CASES.add(case_id)


def is_case_canceled(case_id: str | None) -> bool:
    if not case_id:
        return False
    return case_id in _CANCELED_CASES


def clear_case(case_id: str) -> None:
    with _LOCK:
        _CANCELED_CASES.discard(case_id)


def log(execution_id: str, text: str, level: str = "info", case_id: str | None = None) -> None:
    """记一行执行日志。case_id：这行属于哪条用例(批量执行时用于按用例分别展示日志)；
    None=批次级(如"开始执行/执行完成")对所有用例可见。"""
    if not execution_id or not text:
        return
    with _LOCK:
        dq = _LOGS.get(execution_id)
        if dq is None:
            dq = deque(maxlen=_MAX_LINES)
            _LOGS[execution_id] = dq
        _SEQ[execution_id] += 1
        dq.append({"seq": _SEQ[execution_id], "ts": time.time(), "text": text[:300],
                   "level": level, "case_id": case_id})


def get_logs(execution_id: str, after: int = 0, case_id: str | None = None) -> list[dict]:
    """取 after 之后的新日志。给了 case_id 则只返回【该用例的行 + 批次级(case_id 为空)的行】——
    批量执行时点某条用例只看它自己的日志，不再串到别的用例。"""
    with _LOCK:
        dq = _LOGS.get(execution_id)
        if not dq:
            return []
        out = [e for e in dq if e["seq"] > after]
        if case_id:
            out = [e for e in out if e.get("case_id") in (None, case_id)]
        return out
