# CodeAgent v0 ReAct MVP Plan

## 目标定位

CodeAgent 是一个 Python 技术栈的通用 code agent。第一版先跑通最小 ReAct 闭环，并把 `big-market` 的 text-to-sql 作为后续第一个垂类实战场景。

核心定位：

- 通用 code agent 内核，不把系统写死成 text-to-sql 工具。
- text-to-sql 作为内置 Skill/Workflow，而不是第一版就做成 MCP 或插件。
- 本地参考项目包括 `pico`、`mycodeAgent`、`3_mewcode-python`，只作为设计参考，不整体迁移。
- 主仓库使用 `369pro/CodeAgent`，本地路径为 `/Users/tsy/WorkSpace/LLMproj/CodeAgent`。

## 第一版范围

第一版先实现最小 ReAct 闭环：

```text
用户输入
-> LLM 决策
-> 调用工具
-> 观察工具结果
-> 继续决策
-> 输出最终答复
```

第一版先不做完整 agent 评估，不做 text-to-sql 评估，不做多 agent 并行开发、长期后台任务、自动 PR、IDE 深度集成。

需要提前纳入的能力：

- 自动 git commit。
- 自动 git push。
- 同步到 GitHub 仓库 `369pro/CodeAgent`。

自动 commit/push 的边界：

- agent 只提交自己本轮产生的变更。
- push 前需要明确 remote、branch、commit message。
- 不应把脏工作区里无关改动一起提交。

## LLM 后端

第一版默认接入 DeepSeek。

DeepSeek 官方 API 采用 OpenAI-compatible 格式：

- `base_url`: `https://api.deepseek.com`
- API key 环境变量：`DEEPSEEK_API_KEY`
- 默认模型优先考虑：`deepseek-v4-flash` 或 `deepseek-v4-pro`

配置文件放在项目根目录：

```text
.codeagent/config.yaml
```

密钥不写入仓库，通过环境变量读取。

## 配置约定

推荐配置结构：

```yaml
llm:
  provider: deepseek
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  model: deepseek-v4-flash
  temperature: 0

agent:
  max_steps: 10
  tool_output_limit: 8000

git:
  auto_commit: true
  auto_push: true
  remote: origin
  branch: main
```

## ReAct 最小工具集

第一版工具集保持克制：

- `read_file`
- `list_files` 或 `glob`
- `grep`
- `write_file` 或 `edit_file`
- `bash`
- `git_status`
- `git_diff`
- `git_commit`
- `git_push`

工具系统需要有统一 schema、统一返回值、错误信息和权限控制。

## Text-to-SQL 后续方向

text-to-sql 是后续需求，不进入第一版实现。

入口形态：

- 在 Python code agent CLI 中直接自然语言提问。
- 可以直接返回查询数据。

数据源策略：

- 支持本地开发库和远程测试库。
- 默认本地。
- 切换远程必须显式声明。
- CLI 需要显示当前数据源，例如 `local_dev` 或 `remote_test`。

分库分表策略：

- 不优先做 MySQL 跨库扇出。
- 涉及分库分表的读场景优先查询 Elasticsearch 读模型。
- 普通配置类查询走 MySQL 主库。

意图路由规则：

- 活动、策略、奖品、规则树、SKU、库存配置等配置类查询，优先 MySQL `big_market`。
- 用户抽奖订单、活动订单、参与明细、订单状态统计等读模型场景，优先 Elasticsearch SQL。
- 问题模糊时，agent 先说明准备查询的数据源和表/索引，再执行。
- ES 未覆盖的分片表，不自动扫 MySQL，提示能力边界或要求用户指定方案。

执行策略：

- 默认自动执行只读查询。
- 目标是远程测试库、ES 大索引且无时间范围、预计结果过大、复杂聚合或低置信度时，先展示 SQL 并要求确认。

## Skill / MCP / 插件取舍

第一版把 text-to-sql 做成内置 Skill/Workflow。

原因：

- Skill/Workflow 适合表达垂类任务流程。
- 数据库查询能力可以作为 Tool。
- MCP 更适合把外部能力标准化成跨进程工具服务，后续可用于 DB tools。
- 插件更适合未来分发一组 skill、tools、配置和 MCP server。

推荐结构：

```text
CodeAgent Core
  ReAct loop
  Tool registry
  Session store
  Permission policy

Built-in Workflows
  text_to_sql/
    intent_router
    schema_retriever
    sql_generator
    sql_guard
    sql_executor
    result_formatter

Tools
  mysql_query
  elasticsearch_sql_query
  read_file
  grep
  bash
  git_commit
  git_push
```

## 评估后续方向

评估暂不进入第一版。

后续 text-to-sql 评估维度：

- 意图路由准确率。
- schema grounding。
- SQL 只读和安全校验。
- SQL 可执行性。
- 查询结果正确性。
- 回答解释质量。

后续 agent 评估维度：

- 是否完成任务。
- 工具调用轨迹是否合理。
- 是否遵守权限和安全策略。
- 是否正确处理失败、重试和最终答复。

可观测性工具推荐 Langfuse，原因是其框架无关、开源、可自托管，更适合自研 Python ReAct agent。LangSmith 更适合深度使用 LangChain/LangGraph 的场景。
