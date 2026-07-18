# CodeAgent 项目规范

本文件记录长期有效的项目协作规范。阶段性规格、设计记录、路线图不要放在这里，统一放到 `docs/` 下。

## 项目定位

CodeAgent 是一个 Python 技术栈的通用 code agent。当前优先目标是实现最小 ReAct 闭环，后续会扩展 text-to-sql 等垂类能力。

## 文档结构

- `AGENTS.md`: 项目长期规范，给人和 agent 看的协作约束。
- `docs/specs/`: 阶段性规格与版本方案。每个版本单独建目录，例如 `docs/specs/v1-tools-and-run-records/`，内部按需要放 `spec.md`、`plan.md`、`checklist.md`。
- `docs/design/`: 稳定后的架构设计、模块边界、关键设计说明。实现进入项目代码后，对应设计应沉淀到这里。
- `docs/workflows/`: 垂类工作流说明，例如 text-to-sql。
- `docs/evals/`: 后续评估方案、数据集说明、指标口径。
- 阶段内草案放在对应 `docs/specs/<version>/`；一旦设计进入项目代码并需要长期维护说明，应整理到 `docs/design/`，不要散落到 `docs/adr/` 根目录。

当前版本文档：

- v0 ReAct MVP: `docs/specs/v0-react-mvp/plan.md`
- v1 工具与运行记录: `docs/specs/v1-tools-and-run-records/`
- 系统提示工程化设计: `docs/design/system-prompt-engineering.md`

## 开发约定

- 第一版保持实现克制，优先跑通 ReAct 最小闭环。
- LLM 默认接入 DeepSeek，密钥通过环境变量读取，不写入仓库。
- 配置文件放在 `.codeagent/config.yaml`。
- 自动 git commit/push 只处理 agent 本轮产生的变更，不混入无关工作区改动。
- text-to-sql、评估、多 agent、插件化都属于后续扩展，不阻塞第一版闭环。
