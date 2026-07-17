# v1 Tools and Run Records Spec

## 背景

当前 CodeAgent 已经有最小 ReAct 闭环：用户输入后，模型按文本协议输出 Action，Agent 调用 `list_files`、`read_file`、`grep` 三个内置工具，再把 Observation 回填给模型，直到输出 Final Answer。

这个实现能证明闭环成立，但还不适合继续扩展：

- 工具都集中在 `src/codeagent/tools.py`，无法支撑更多工具、统一 schema、权限分类和测试隔离。
- 工具集合还不完整，只覆盖了最小只读场景，缺少文件写入、精确编辑、命令执行、glob 等 code agent 常用能力。
- ReAct 执行完成后只返回内存里的 `RunResult`，没有持久化一次运行的 prompt、模型输出、工具调用、工具结果、耗时、错误等信息，不方便复盘。

本版本参考 `/Users/tsy/WorkSpace/3_mewcode-python` 的工具组织和记录方式，但只吸收适合当前 CodeAgent 阶段的核心工具集，不引入多 agent、team、worktree、MCP、skill 安装等更大系统。

## 目标

- 把工具系统重构为独立 `tools` 包，每个工具独立实现，registry 统一注册和执行。
- 补齐当前阶段 code agent 需要的核心工具：读文件、写文件、编辑文件、glob、grep、bash、git 状态、git diff。
- 为写入类工具建立基本安全约束，避免模型在未读取上下文的情况下直接覆盖已有文件。
- ReAct 每次运行后生成可复盘记录，包含用户输入、模型决策、工具调用、工具输出、最终回答、错误和基础耗时信息。
- 保持 CLI 使用方式简单，后续版本可以基于记录文件做 replay、debug、评估和总结。

## 功能需求

- F1: 工具必须作为独立包组织，不再把所有工具实现放在单个 `tools.py` 文件中。
- F2: 工具必须有统一的元数据和执行返回结构，至少包含工具名、描述、参数 schema、类别、是否错误、输出文本。
- F3: 工具 registry 必须支持注册、查找、列出、执行工具，并能生成给 ReAct prompt 使用的工具描述。
- F4: 必须实现 `read_file`，支持读取文件内容，并返回带行号的文本。
- F5: 必须实现 `write_file`，支持创建或覆盖文件；覆盖已有文件前必须要求该文件已被读取过。
- F6: 必须实现 `edit_file`，支持用唯一的 `old_string` 精确替换为 `new_string`；编辑前必须要求文件已被读取过。
- F7: 必须实现 `glob`，支持按 glob pattern 查找文件，并跳过 `.git`、`.venv`、`node_modules`、`__pycache__` 等目录。
- F8: 必须实现 `grep`，支持按正则搜索文件内容，返回 `file:line:content` 格式。
- F9: 必须实现 `bash`，支持执行 shell 命令，捕获 stdout/stderr，标记非零退出码，并有超时上限。
- F10: 必须实现 `git_status` 和 `git_diff`，用于查看工作区状态和差异；它们是只读 git 工具。
- F11: ReAct 循环必须能调用新 registry 里的所有工具，并把工具错误作为 Observation 回填给模型，而不是直接崩溃。
- F12: 每次 ReAct 运行必须生成一个 run record，记录完整步骤和最终结果。
- F13: run record 必须持久化到本地目录，默认不提交到 git。
- F14: CLI 在交互模式下必须显示本次运行记录路径，方便用户事后打开复盘。

## 非功能需求

- N1: 第一版继续保持标准库优先，不为了工具 schema 引入重依赖；如果需要类型校验，优先用 dataclass 和轻量手写校验。
- N2: 工具执行必须限制在配置的 workspace 内，除非后续版本显式加入权限系统。
- N3: 记录文件必须是可读、可 diff、可被后续脚本消费的 JSON。
- N4: 记录中不要主动写入 API key、环境变量完整值等敏感信息。
- N5: 写入类工具失败时必须返回可读错误信息，说明是未先读取、文件变化、路径越界、字符串不唯一还是其他原因。
- N6: ReAct 运行记录不能因为记录写入失败而吞掉原始 agent 结果；记录失败需要被报告。

## 不做的事

- 不实现多 agent、team、worktree、MCP、skill 安装、tool search、ask user 等参考项目里的高级工具；本版本只实现核心工具集。
- 不实现 git commit、git push 自动执行工具；它们会改变远端状态，留到权限系统明确后再做。
- 不实现完整权限审批 UI。
- 不实现 replay、评估报表、token 成本统计，只为这些后续能力保留记录字段。
- 不迁移参考项目的全部依赖和异步架构，本版本以当前同步 ReAct 循环为主。
- 不生成 `task.md`；本次文档阶段只维护 spec、plan、checklist。

## 验收标准

- AC1: 项目中存在独立 `src/codeagent/tools/` 包，工具实现按文件拆分，旧的单文件工具实现被替换或只保留兼容导出。
- AC2: 默认 registry 能列出 `read_file`、`write_file`、`edit_file`、`glob`、`grep`、`bash`、`git_status`、`git_diff`。
- AC3: ReAct 测试中可以用 fake LLM 连续调用至少三个不同工具，并得到最终答案。
- AC4: `write_file` 和 `edit_file` 在未先读取已有文件时拒绝操作，并返回可观察错误。
- AC5: `grep` 和 `glob` 会跳过常见缓存/依赖目录。
- AC6: `bash` 超时时会终止命令并返回错误结果；非零退出码不会导致 Python traceback。
- AC7: 每次 CLI 提问完成后，本地生成一份 JSON run record。
- AC8: run record 至少包含 run id、时间、workspace、用户输入、每一步模型输出、工具名、工具参数、工具结果、是否错误、最终回答、运行状态。
- AC9: `.gitignore` 忽略 run record 默认目录和 `.codeagent/` 本地配置目录。
- AC10: 所有单元测试通过，且不依赖真实 DeepSeek 网络调用。

## 已确认决策

- D1: “实现所有工具”限定为当前 code agent 核心工具集，不纳入参考项目 `mewcode/tools/` 下的 `AgentTool`、task/team/worktree/skill 安装工具。
- D2: 工具名称继续使用小写 snake_case，例如 `read_file`、`git_status`。
- D3: run record 默认目录使用 `.codeagent/runs/`，并保持 git ignore。
