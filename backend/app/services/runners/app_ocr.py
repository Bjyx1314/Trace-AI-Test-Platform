"""OCR 文字定位：在自绘(Flutter/RN)App 里，控件不在无障碍树、AI 报坐标又不精准时，
用 OCR 把某个【确切文案】的文字框直接找出来、点它中心——确定性、精准，作为"视觉猜坐标"之前的一层。

引擎用 rapidocr-onnxruntime(纯 pip、CPU、中文效果不错)。惰性加载：引擎装不上/加载失败就返回 None，
调用方自动回退到原有视觉流程，绝不因缺 OCR 而报错。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_engine = None
_tried = False


def _get_engine():
    global _engine, _tried
    if _tried:
        return _engine
    _tried = True
    try:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
        logger.info("OCR 引擎已就绪(rapidocr-onnxruntime)")
    except Exception as e:  # noqa: BLE001 缺引擎/加载失败→降级为纯视觉
        logger.warning("OCR 引擎不可用，降级为纯视觉定位：%s", e)
        _engine = None
    return _engine


def available() -> bool:
    return _get_engine() is not None


def locate_text(img, target: str, near: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """在 PIL 图(设备分辨率)上 OCR，返回【包含 target 文案】的文字框中心(设备像素 x,y)。
    找不到 / 引擎不可用 → None。
    - near 给了(AI 想点的大致坐标)时：在匹配框中优先选【离 near 最近】的那个(先精确匹配再最近)——
      这样既用 AI 的意图(点哪个实例)、又用 OCR 的精度(准确坐标)。
    - near 没给时：优先完全等于 target、分数高、偏上的框。"""
    eng = _get_engine()
    target = (target or "").strip()
    if eng is None or not target:
        return None
    try:
        import numpy as np
        arr = np.array(img.convert("RGB"))
        result, _ = eng(arr)
        if not result:
            return None
        best = None  # (排序键, (cx,cy))
        for item in result:
            try:
                box, text, score = item[0], item[1], float(item[2])
            except Exception:
                continue
            t = (text or "").strip()
            if not t:
                continue
            exact = (t == target)
            if not (exact or target in t or t in target):
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys))
            if near is not None:
                d2 = (cx - near[0]) ** 2 + (cy - near[1]) ** 2
                key = (0 if exact else 1, d2)            # 先精确匹配 → 再离 AI 坐标最近
            else:
                key = (0 if exact else 1, -score, cy)    # 先精确 → 分高 → 偏上
            if best is None or key < best[0]:
                best = (key, (cx, cy))
        return best[1] if best else None
    except Exception as e:  # noqa: BLE001
        logger.info("OCR 定位异常(忽略)：%s", e)
        return None
