"""代码事实图谱（方案 9，阶段五，归属 AI 执行模块）。

子模块：
- nodes: 稳定节点 ID 生成（方案 9.3）
- builder: 消费 code-graph-scan skill 输出，全量事实导入节点/边
- incremental: MR 增量更新 + rename 合并
- runtime_collector: 执行/录制回补 runtime 边（Case→Page、Page→API）
- expander: 影响面扩散（双向 BFS + 证据链）
- staleness: 陈旧性治理
"""
from . import nodes, builder, runtime_collector, expander, staleness, incremental  # noqa: F401
