"""App 测试包版本查询 / 包版本→下载信息解析（执行前换包用）。

开源发行版不预置业务 APK。接入自己的制品库后，只需替换下面两个函数的数据源，
函数签名与返回结构保持不变即可。

返回结构：
- list_packages(app) -> [{"id","label","version"}]      # 下拉选项
- resolve_package(app, package_id) -> {"source","package"} | None
    source: 交给 apk.install_apk 的来源（http(s)://… / local:<名字> / 绝对路径）
    package: 旧包的 android 包名（用于卸载旧包）；None 则由 apk 侧尝试解析，失败退化为覆盖安装
"""
from __future__ import annotations

_TEST_PACKAGES: dict[str, dict] = {}


def list_packages(app: str) -> list[dict]:
    """某个 app 端可选的测试包版本列表（默认空，供制品库适配器替换）。"""
    return list(_TEST_PACKAGES.get(app, {}).get("versions", []))


def resolve_package(app: str, package_id: str) -> dict | None:
    """把所选包版本解析成 {source, package}（apk 下载来源 + 旧包名）。"""
    return _TEST_PACKAGES.get(app, {}).get("resolve", {}).get(package_id)
