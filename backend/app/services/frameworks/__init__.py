"""框架集成服务包 —— 框架仓库绑定 / 索引扫描 / 仓库内执行。

设计目标（详见任务 b921b61f 改造方案）：把已有的成熟自动化框架（接口 AWFunc 数据驱动、
PC/App POM）以 git 仓库形式绑定到平台，平台按框架原生风格生成用例、提交回仓库长期沉淀，
执行时在仓库 checkout 内跑框架自身命令。

模块划分：
- repo_manager：clone/pull 框架仓库到本地工作区，提供 commit sha。
- scanner：AST 扫描框架"积木"（接口=AW关键字清单；UI=pages/flows/components/fixtures），纯函数。
- indexer：编排 repo_manager + scanner，把结果写回 FrameworkRepo.index_json。
"""
