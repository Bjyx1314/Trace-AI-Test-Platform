"""Feishu (Lark) 自建应用集成：tenant_access_token获取、Bitable需求同步、缺陷自动建单。

与 feishu.py（机器人Webhook通知，Project.feishu_webhook）是两套独立机制：
本模块基于自建应用 app_id/app_secret 调用飞书开放平台 API。
"""
from __future__ import annotations
import re
import time
from urllib.parse import urlparse, parse_qs
import httpx
from app.config import settings
from app.models import Defect

_OPEN_API_BASE = "https://open.feishu.cn/open-apis"

_token_cache: dict = {"token": None, "expires_at": 0.0}


class FeishuError(Exception):
    """飞书接口调用失败，携带可直接展示给用户的中文提示。"""
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# 常见飞书错误码 → 中文提示
_FEISHU_ERROR_ZH = {
    131005: "飞书文档/知识库节点不存在，请检查链接是否正确",
    131006: "飞书权限不足：应用没有该文档/知识库的读取权限，请将应用加入对应知识库或文档协作者（可阅读）",
    99991672: "飞书应用未开通所需权限范围，请在开放平台开通 wiki/docx 读取权限并发布版本",
    99991663: "飞书应用凭据无效，请检查 app_id / app_secret",
    1254005: "飞书文档不存在或已被删除",
    1254043: "飞书文档无访问权限，请将应用加入协作者",
}


def _feishu_zh(code: int | None, msg: str) -> str:
    if code in _FEISHU_ERROR_ZH:
        return _FEISHU_ERROR_ZH[code]
    return f"飞书接口返回错误（code={code}）：{msg or '未知错误'}"


def _app_configured() -> bool:
    # 飞书集成只看应用凭据是否配置，独立于全局 MOCK_MODE
    return bool(settings.feishu_app_id and settings.feishu_app_secret)


async def get_tenant_access_token() -> str | None:
    """获取并缓存tenant_access_token（提前60秒过期）。凭据缺失或MOCK_MODE时返回None。"""
    if not _app_configured():
        return None
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{_OPEN_API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(_feishu_zh(data.get("code"), data.get("msg", "")), data.get("code"))
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = now + data.get("expire", 7200) - 60
    return _token_cache["token"]


_MOCK_BITABLE_REQUIREMENTS = [
    {"record_id": "MOCK-REC-001", "title": "购物车结算流程",
     "content": "用户添加商品至购物车后，应能正确计算总价并完成结算。",
     "product_line": "main_app", "iteration": "sprint-1"},
    {"record_id": "MOCK-REC-002", "title": "个人中心头像上传",
     "content": "用户在个人中心页面应能上传并更新头像，支持jpg/png格式。",
     "product_line": "main_app", "iteration": "sprint-1"},
]


def _parse_bitable_record_link(link: str) -> tuple[str, str, str] | None:
    """解析飞书多维表格记录分享链接，提取(app_token, table_id, record_id)。
    支持形如 https://xxx.feishu.cn/base/{app_token}?table={table_id}&record={record_id} 的链接。"""
    parsed = urlparse(link)
    m = re.search(r"/base/([a-zA-Z0-9]+)", parsed.path)
    if not m:
        return None
    app_token = m.group(1)
    qs = parse_qs(parsed.query)
    table_id = qs.get("table", [None])[0]
    record_id = qs.get("record", [None])[0]
    if not table_id or not record_id:
        return None
    return app_token, table_id, record_id


async def fetch_bitable_record_by_link(link: str) -> dict | None:
    """根据飞书多维表格记录分享链接获取单条需求记录，返回{record_id,title,content,product_line}。
    MOCK_MODE或缺少凭据时返回mock记录；链接缺少table/record参数且非mock时返回None。"""
    parsed = _parse_bitable_record_link(link)
    # 配了凭据就真拉；没配：本地返回 mock，服务器真实环境直接报错(不产生 mock 数据)
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        if not settings.mock_allowed:
            raise FeishuError("未配置飞书应用凭据(FEISHU_APP_ID/SECRET)，无法同步需求")
        record_id = parsed[2] if parsed else "MOCK-LINK-RECORD"
        return {
            "record_id": record_id,
            "title": "飞书文档同步需求",
            "content": f"通过链接同步的需求记录（mock）。\n原始链接: {link}",
            "product_line": "main_app",
            "iteration": None,
        }

    if not parsed:
        raise FeishuError("链接解析失败：多维表格链接需包含 table 与 record 参数")
    app_token, table_id, record_id = parsed
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(_feishu_zh(data.get("code"), data.get("msg", "")), data.get("code"))
    fields = data.get("data", {}).get("record", {}).get("fields", {})
    return {
        "record_id": record_id,
        "title": fields.get("title") or fields.get("标题") or "",
        "content": fields.get("content") or fields.get("内容") or "",
        "product_line": fields.get("product_line") or fields.get("产品线"),
        "iteration": fields.get("iteration") or fields.get("迭代") or None,
    }


def _parse_wiki_node_token(link: str) -> str | None:
    """从飞书知识库链接解析 node_token，形如 https://xxx.feishu.cn/wiki/{node_token}。"""
    parsed = urlparse(link)
    m = re.search(r"/wiki/([a-zA-Z0-9]+)", parsed.path)
    return m.group(1) if m else None


async def _fetch_docx_raw_content(document_id: str, token: str) -> str | None:
    """读取新版文档(docx)纯文本全文。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_OPEN_API_BASE}/docx/v1/documents/{document_id}/raw_content",
            headers={"Authorization": f"Bearer {token}"},
            params={"lang": 0},
        )
        data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(_feishu_zh(data.get("code"), data.get("msg", "")), data.get("code"))
    return data.get("data", {}).get("content", "")


# 飞书 docx block_type → 文本字段名（带 elements 的块）
_DOCX_TEXT_KEY = {
    2: "text", 3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4", 7: "heading5",
    8: "heading6", 9: "heading7", 10: "heading8", 11: "heading9",
    12: "bullet", 13: "ordered", 14: "code", 15: "quote", 17: "todo",
}


def _docx_block_text(block: dict, key: str) -> str:
    node = block.get(key) or {}
    parts = []
    for e in node.get("elements", []) or []:
        tr = e.get("text_run")
        if tr and tr.get("content"):
            parts.append(tr["content"])
    return "".join(parts).strip()


def _docx_blocks_to_markdown(items: list[dict], media_map: dict | None = None) -> str:
    """把飞书 docx blocks 重建为 Markdown，尽量保真：标题/列表/表格/图片。
    media_map: {image_token: 可访问URL}，命中则渲染为 ![图片](url)，否则 [图片] 占位。"""
    media_map = media_map or {}
    by_id = {b.get("block_id"): b for b in items}
    root = next((b for b in items if b.get("block_type") == 1), None)
    if root is None:
        return ""

    def render(bid: str, depth: int = 0) -> str:
        b = by_id.get(bid)
        if not b:
            return ""
        bt = b.get("block_type")
        own = ""
        if bt in _DOCX_TEXT_KEY:
            txt = _docx_block_text(b, _DOCX_TEXT_KEY[bt])
            indent = "  " * depth
            if 3 <= bt <= 11:
                own = ("#" * (bt - 2)) + " " + txt
            elif bt == 12:
                own = f"{indent}- {txt}"
            elif bt == 13:
                own = f"{indent}1. {txt}"
            elif bt == 14:
                own = f"```\n{txt}\n```"
            elif bt == 15:
                own = f"{indent}> {txt}"
            elif bt == 17:
                own = f"{indent}- [ ] {txt}"
            else:
                own = f"{indent}{txt}" if depth else txt
        elif bt == 27:  # image
            img_token = (b.get("image") or {}).get("token")
            url = media_map.get(img_token)
            own = f"![图片]({url})" if url else "[图片]"
        elif bt == 22:  # divider
            own = "---"
        elif bt == 31:  # table（自行处理子单元格）
            return _render_table(b)

        # 递归渲染子块（嵌套列表/段落等），列表项的子项缩进一级
        parts = [own] if own else []
        child_depth = depth + 1 if bt in (12, 13, 17) else depth
        for ch in (b.get("children") or []):
            sub = render(ch, child_depth)
            if sub:
                parts.append(sub)
        return "\n".join(p for p in parts if p)

    def _render_table(tb: dict) -> str:
        prop = (tb.get("table") or {}).get("property") or {}
        cols = prop.get("column_size") or 1
        cell_ids = tb.get("children") or []
        # 每个 cell(32) 的文本 = 其子块渲染后压平为单行
        cells = []
        for cid in cell_ids:
            cell = by_id.get(cid) or {}
            inner = "\n".join(render(ch) for ch in (cell.get("children") or []))
            cells.append(inner.replace("\n", " ").replace("|", "\\|").strip() or " ")
        if not cells:
            return ""
        rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
        # 补齐残行
        rows = [r + [" "] * (cols - len(r)) for r in rows]
        md = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * cols) + " |"]
        for r in rows[1:]:
            md.append("| " + " | ".join(r) + " |")
        return "\n".join(md)

    blocks = [render(ch) for ch in (root.get("children") or [])]
    return "\n\n".join(x for x in blocks if x).strip()


from pathlib import Path as _Path

# 飞书文档图片本地存储目录（与需求附件同一 uploads 根），按图片 token 命名
_MEDIA_DIR = _Path(__file__).resolve().parents[2] / "uploads" / "feishu_media"
_MEDIA_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


async def _download_docx_image(client: httpx.AsyncClient, img_token: str, token: str) -> str | None:
    """下载飞书文档图片到本地，返回平台可访问 URL；失败返回 None。"""
    try:
        resp = await client.get(
            f"{_OPEN_API_BASE}/drive/v1/medias/{img_token}/download",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200 or "image" not in resp.headers.get("content-type", ""):
            return None
        ext = _MEDIA_EXT.get(resp.headers.get("content-type", "").split(";")[0], ".png")
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        (_MEDIA_DIR / f"{img_token}{ext}").write_bytes(resp.content)
        return f"/api/requirements/media/{img_token}"
    except Exception:
        return None


async def _fetch_docx_markdown(document_id: str, token: str) -> str | None:
    """读取 docx 全部 block 并重建 Markdown（保留表格/标题/列表，图片下载到本地并内联）。失败返回 None。"""
    items: list[dict] = []
    page_token = None
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            resp = await client.get(
                f"{_OPEN_API_BASE}/docx/v1/documents/{document_id}/blocks",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            data = resp.json()
            if data.get("code") != 0:
                return None
            items += data.get("data", {}).get("items", []) or []
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data["data"].get("page_token")

        # 下载图片并构建 token→URL 映射
        media_map: dict[str, str] = {}
        for b in items:
            if b.get("block_type") == 27:
                tk = (b.get("image") or {}).get("token")
                if tk and tk not in media_map:
                    url = await _download_docx_image(client, tk, token)
                    if url:
                        media_map[tk] = url
    return _docx_blocks_to_markdown(items, media_map) or None


async def _fetch_docx_title(document_id: str, token: str) -> str | None:
    """读取新版文档(docx)标题。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_OPEN_API_BASE}/docx/v1/documents/{document_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
    if data.get("code") != 0:
        return None
    return data.get("data", {}).get("document", {}).get("title")


async def fetch_wiki_document_by_link(link: str) -> dict | None:
    """根据飞书知识库(wiki)链接同步单条需求：先解析 wiki 节点拿到挂载的文档(obj_token)，
    再读取文档正文。返回 {record_id, title, content, product_line, iteration}。
    title=文档标题，content=全文正文。MOCK_MODE或缺少凭据时返回mock记录。"""
    node_token = _parse_wiki_node_token(link)
    # 配了凭据就真拉；没配：本地返回 mock，服务器真实环境直接报错(不产生 mock 数据)
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        if not settings.mock_allowed:
            raise FeishuError("未配置飞书应用凭据(FEISHU_APP_ID/SECRET)，无法同步需求")
        return {
            "record_id": node_token or "MOCK-WIKI-NODE",
            "title": "飞书知识库同步需求",
            "content": f"通过知识库链接同步的需求文档（mock）。\n原始链接: {link}",
            "product_line": None,
            "iteration": None,
        }

    if not node_token:
        raise FeishuError("链接解析失败：未识别到知识库节点，请确认是 /wiki/ 开头的飞书知识库链接")
    token = await get_tenant_access_token()

    # 1) 解析 wiki 节点 → obj_token / obj_type / 节点标题
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_OPEN_API_BASE}/wiki/v2/spaces/get_node",
            headers={"Authorization": f"Bearer {token}"},
            params={"token": node_token},
        )
        node_data = resp.json()
    if node_data.get("code") != 0:
        raise FeishuError(_feishu_zh(node_data.get("code"), node_data.get("msg", "")), node_data.get("code"))
    node = node_data.get("data", {}).get("node", {})
    obj_token = node.get("obj_token")
    obj_type = node.get("obj_type")
    node_title = node.get("title") or ""
    if not obj_token:
        raise FeishuError("该知识库链接未挂载文档内容，无法同步")
    # 仅支持文档型节点（新版 docx / 旧版 doc）；其它类型（表格/多维表格等）暂不支持按文档同步
    if obj_type not in ("docx", "doc"):
        raise FeishuError(f"该知识库节点为「{obj_type}」类型，暂不支持按文档同步（仅支持 docx / doc 文档）")

    # 2) 读取文档正文：优先用 blocks 重建 Markdown(保真表格/标题/图片)，失败回退纯文本
    content = await _fetch_docx_markdown(obj_token, token)
    if not content:
        content = await _fetch_docx_raw_content(obj_token, token)
    title = node_title or (await _fetch_docx_title(obj_token, token)) or "未命名需求"
    return {
        "record_id": node_token,
        "title": title,
        "content": content,
        "product_line": None,
        "iteration": None,
    }


async def fetch_feishu_requirement_by_link(link: str) -> dict | None:
    """飞书链接同步分发：按链接类型选择多维表格(/base/)或知识库文档(/wiki/)同步。"""
    if "/wiki/" in link:
        return await fetch_wiki_document_by_link(link)
    return await fetch_bitable_record_by_link(link)


async def fetch_bitable_requirements() -> list[dict]:
    """从飞书多维表格读取需求记录，返回[{record_id,title,content,product_line}]。
    MOCK_MODE或缺少app_token/table_id时返回mock数据。
    期望Bitable字段名: title/content/product_line（同时兼容"标题"/"内容"/"产品线"）。"""
    if not (settings.feishu_bitable_app_token and settings.feishu_requirements_table_id):
        if not settings.mock_allowed:
            raise FeishuError("未配置飞书多维表格(FEISHU_BITABLE_APP_TOKEN/TABLE_ID)，无法批量同步")
        return _MOCK_BITABLE_REQUIREMENTS

    token = await get_tenant_access_token()
    if not token:
        return _MOCK_BITABLE_REQUIREMENTS

    records: list[dict] = []
    page_token = None
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            resp = await client.get(
                f"{_OPEN_API_BASE}/bitable/v1/apps/{settings.feishu_bitable_app_token}"
                f"/tables/{settings.feishu_requirements_table_id}/records",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(_feishu_zh(data.get("code"), data.get("msg", "")), data.get("code"))
            for item in data.get("data", {}).get("items", []):
                fields = item.get("fields", {})
                records.append({
                    "record_id": item["record_id"],
                    "title": fields.get("title") or fields.get("标题") or "",
                    "content": fields.get("content") or fields.get("内容") or "",
                    "product_line": fields.get("product_line") or fields.get("产品线"),
                    "iteration": fields.get("iteration") or fields.get("迭代") or None,
                })
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data["data"].get("page_token")
    return records


async def create_defect_ticket(defect: Defect) -> str | None:
    """缺陷确认后在飞书多维表格"缺陷"表创建一行作为工单，返回record_id。
    MOCK_MODE或缺少app_token/table_id时返回mock工单号。
    期望Bitable字段名: title/severity/status/summary/affected_scope/type。"""
    if not (settings.feishu_bitable_app_token and settings.feishu_defects_table_id):
        if not settings.mock_allowed:
            raise FeishuError("未配置飞书缺陷多维表格(FEISHU_BITABLE_APP_TOKEN/DEFECTS_TABLE_ID)，无法建单")
        return f"MOCK-TICKET-{defect.id[:8]}"

    token = await get_tenant_access_token()

    draft = defect.draft_ticket or {}
    fields = {
        "title": defect.title,
        "severity": defect.severity,
        "status": defect.status,
        "summary": draft.get("summary", ""),
        "affected_scope": draft.get("affected_scope", ""),
        "type": draft.get("type", ""),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{_OPEN_API_BASE}/bitable/v1/apps/{settings.feishu_bitable_app_token}"
            f"/tables/{settings.feishu_defects_table_id}/records",
            headers={"Authorization": f"Bearer {token}"},
            json={"fields": fields},
        )
        data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(_feishu_zh(data.get("code"), data.get("msg", "")), data.get("code"))
    return data.get("data", {}).get("record", {}).get("record_id")
