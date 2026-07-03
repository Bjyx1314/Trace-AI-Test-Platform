"""需求文档解析：从上传的文档文件中提取纯文本内容，并推导标题。"""
from __future__ import annotations

import io

DOCUMENT_EXTENSIONS = {"txt", "md", "docx", "pdf"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def extract_text_from_file(filename: str, content: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md"):
        return content.decode("utf-8", errors="ignore")
    if ext == "docx":
        from docx import Document

        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise ValueError(f"不支持的文档类型: .{ext}")


def derive_title(content: str, filename: str) -> str:
    """取正文首个非空行（去除Markdown标题符号）作为标题，失败则回退到文件名。"""
    for line in content.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    stem = filename.rsplit(".", 1)[0]
    return stem[:200] or filename
