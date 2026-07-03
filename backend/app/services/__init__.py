"""services 包。

包级便捷再导出改为懒加载(PEP 562 __getattr__)：仅导入子模块(如执行机 worker 只用
devices / runners)时不再被动拉入 mock_runner→sqlalchemy 等重依赖，便于 worker 瘦身打包。
"""


def __getattr__(name):
    if name == "MockExecutionRunner":
        from .mock_runner import MockExecutionRunner
        return MockExecutionRunner
    if name == "send_feishu_notification":
        from .feishu import send_feishu_notification
        return send_feishu_notification
    raise AttributeError(f"module 'app.services' has no attribute {name!r}")
