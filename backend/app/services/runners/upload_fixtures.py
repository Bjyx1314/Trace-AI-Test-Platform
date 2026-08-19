"""上传用测试文件生成器（web/android runner 共享）。

为什么需要：涉及"上传图片/附件"的用例，执行端必须【自己拿得出文件】。此前 PC 端只能让 AI
去点上传控件——点开的是【操作系统原生文件对话框】，Playwright 既截不到它、它还阻塞页面，
于是永远"点了没反应"，这类用例必然判 blocked（线上 TC-ZN-0489 就是连点 3 步全卡住）。

设计要点：
- 【尺寸精确】：JPEG 在 EOI 标记之后追加填充字节，解码器一律忽略，但文件大小就是校验看到的
  大小。这样"单张 10MB 上限"这类边界用例能造出 9.9MB / 10.1MB 的精确样本，不用碰运气。
- 【内容可辨识】：每张图印上序号，AI 核对"上传了第几张/共几张"时看得出来。
- 【按需缓存】：同一 (尺寸, 序号) 只生成一次，30 张连传不会每步重算。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_JPEG_EOI = b"\xff\xd9"
_CACHE_DIR = Path(tempfile.gettempdir()) / "tp_upload_fixtures"

# 默认单张大小：足够小(30 张也才 ~9MB)，又不会小到被"最小尺寸"校验挡下
DEFAULT_SIZE_KB = 300
MAX_COUNT = 60          # 单步最多造这么多张，防用例写"上传1000张"把磁盘打满


def _base_jpeg(index: int) -> bytes:
    """一张带序号的小 JPEG。用噪点底图，避免纯色被压到几百字节导致填充比例失真。"""
    from io import BytesIO
    from PIL import Image, ImageDraw

    import random
    rnd = random.Random(index)          # 固定种子：同序号每次生成的图一致
    img = Image.new("RGB", (640, 480))
    px = img.load()
    for y in range(0, 480, 8):          # 每 8 像素一块噪点，够抗压缩又不慢
        for x in range(0, 640, 8):
            c = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
            for dy in range(8):
                for dx in range(8):
                    px[x + dx, y + dy] = c
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 380, 110], fill=(255, 255, 255))
    draw.text((40, 50), f"TEST IMAGE #{index}", fill=(0, 0, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def make_images(count: int = 1, size_kb: int = DEFAULT_SIZE_KB, start_index: int = 1) -> list[str]:
    """造 count 张【各自约 size_kb】的 JPEG，返回绝对路径列表（已按需缓存）。

    size_kb 是【精确】的最终文件大小（不小于底图本身大小）；用于"单张不超过 10MB"这类
    边界校验时，传 10*1024-1 与 10*1024+1 即可分别造出刚好合规/刚好超限的样本。
    """
    count = max(1, min(int(count or 1), MAX_COUNT))
    size_kb = max(1, int(size_kb or DEFAULT_SIZE_KB))
    target = size_kb * 1024
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    for i in range(start_index, start_index + count):
        f = _CACHE_DIR / f"upload_{size_kb}kb_{i}.jpg"
        if not (f.exists() and f.stat().st_size == target):
            data = _base_jpeg(i)
            if len(data) < target:
                # 在 EOI 之后填充：解码器忽略，文件大小即校验看到的大小
                data = data + b"\x00" * (target - len(data))
            elif len(data) > target:
                # 底图已超过目标(目标极小时)：只能给底图本身，记一条日志免得静默偏差
                logger.info("上传样本目标 %dKB 小于底图 %dB，实际用底图大小", size_kb, len(data))
            f.write_bytes(data)
        paths.append(str(f))
    return paths


def parse_upload_request(act: dict, step_text: str = "") -> tuple[int, int]:
    """从 AI 的 upload 动作(+步骤文案兜底)解析出 (张数, 单张KB)。

    AI 给了 count/size_kb/size_mb 就用它的；没给就从步骤文案里抠数字（"上传30张"→30，
    "大于10M"→稍微超过 10MB）。解析不出就用默认值，绝不为了"精确"而不上传。
    """
    import re

    count = act.get("count")
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = None
    if not count:
        m = re.search(r"(\d+)\s*张", step_text or "")
        count = int(m.group(1)) if m else 1

    size_kb = act.get("size_kb")
    try:
        size_kb = int(size_kb)
    except (TypeError, ValueError):
        size_kb = None
    if not size_kb and act.get("size_mb"):
        try:
            size_kb = int(float(act["size_mb"]) * 1024)
        except (TypeError, ValueError):
            size_kb = None
    if not size_kb:
        # "大于/超过 10M" → 造 10MB+64KB；"不超过/小于 10M" → 造 10MB-64KB。
        # 负向断言不可省：「不超过10M」里也含"超过"，漏了就会把合规样本造成超限样本，
        # 让一条"应上传成功"的用例莫名其妙地被系统拦下。
        m = re.search(r"(?<![不未])(大于|超过|超出|多于)\s*(\d+(?:\.\d+)?)\s*(M|MB|m|兆)", step_text or "")
        if m:
            size_kb = int(float(m.group(2)) * 1024) + 64
        else:
            m = re.search(r"(小于|不超过|不大于|符合).{0,6}?(\d+(?:\.\d+)?)\s*(M|MB|m|兆)", step_text or "")
            size_kb = (int(float(m.group(2)) * 1024) - 64) if m else DEFAULT_SIZE_KB
    return max(1, min(count, MAX_COUNT)), max(1, size_kb)
