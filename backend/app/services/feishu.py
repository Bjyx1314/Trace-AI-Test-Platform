"""Feishu (Lark) webhook notification service."""
from __future__ import annotations
import httpx
from app.config import settings


async def send_feishu_notification(
    webhook_url: str,
    title: str,
    content: str,
    pass_rate: float | None = None,
) -> bool:
    if not webhook_url:
        return False

    color = "green" if (pass_rate or 0) >= 80 else "red"
    body = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": content},
                }
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=body)
            return resp.status_code == 200
    except Exception:
        return False
