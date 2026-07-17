# CodeAgent 项目规范

本文件记录长期有效的项目协作规范。阶段性方案、设计记录、路线图不要放在这里，统一放到 `docs/` 下。

## 项目定位

CodeAgent 是一个 Python 技术栈的通用 code agent。当前优先目标是实现最小 ReAct 闭环，后续会扩展 text-to-sql 等垂类能力。

## 文档结构

- `AGENTS.md`: 项目长期规范，给人和 agent 看的协作约束。
- `docs/plans/`: 阶段性方案、MVP 范围、路线图。
- `docs/design/`: 稳定后的架构设计、模块边界、关键 ADR。
- `docs/workflows/`: 垂类工作流说明，例如 text-to-sql。
- `docs/evals/`: 后续评估方案、数据集说明、指标口径。

当前第一版方案见 `docs/plans/v0-react-mvp.md`。

## 开发约定

- 第一版保持实现克制，优先跑通 ReAct 最小闭环。
- LLM 默认接入 DeepSeek，密钥通过环境变量读取，不写入仓库。
- 配置文件放在 `.codeagent/config.yaml`。
- 自动 git commit/push 只处理 agent 本轮产生的变更，不混入无关工作区改动。
- text-to-sql、评估、多 agent、插件化都属于后续扩展，不阻塞第一版闭环。
