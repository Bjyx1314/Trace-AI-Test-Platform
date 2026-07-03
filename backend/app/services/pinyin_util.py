"""把中文姓名转成拼音账号（如 张三 → zhangsan）。

用于用户管理把账号(username)统一为姓名拼音。非中文字符按原样保留(小写)，
取消声调、去空格；姓在前名在后，连写不带分隔符，符合邮箱前缀习惯。
"""
from __future__ import annotations

import re


def name_to_pinyin(name: str | None) -> str:
    """返回姓名的连写拼音(小写)。无法转换/为空时返回空串。"""
    s = (name or "").strip()
    if not s:
        return ""
    try:
        from pypinyin import lazy_pinyin, Style
        parts = lazy_pinyin(s, style=Style.NORMAL, errors="default")
        out = "".join(parts)
    except Exception:
        out = s
    # 仅保留字母数字，统一小写(去掉空格/标点/声调残留)
    out = re.sub(r"[^A-Za-z0-9]", "", out).lower()
    return out
