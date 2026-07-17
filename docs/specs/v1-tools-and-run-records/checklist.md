# v1 Tools and Run Records Checklist

> 每一项都通过运行代码或观察行为验证。这里不规定实现顺序，只定义完成后如何验收。

## 文档与范围

- [ ] spec、plan、checklist 三份文档存在，且没有生成 `task.md`。（验证：查看 `docs/specs/v1-tools-and-run-records/`）
- [ ] 文档明确排除了 team、task、worktree、MCP、skill 安装等高级工具。（验证：阅读 spec 的“不做的事”）
- [ ] 文档明确记录“实现所有工具”范围已限定为核心工具集。（验证：阅读 spec/plan 的“已确认决策”）

## 工具组织

- [ ] 项目存在 `src/codeagent/tools/` 包。（验证：运行 `find src/codeagent/tools -maxdepth 2 -type f`）
- [ ] 每个核心工具有独立模块。（验证：看到 `read_file.py`、`write_file.py`、`edit_file.py`、`glob.py`、`grep.py`、`bash.py`、`git_status.py`、`git_diff.py`）
- [ ] registry 能列出所有默认工具。（验证：单元测试断言默认工具名集合包含 `read_file`、`write_file`、`edit_file`、`glob`、`grep`、`bash`、`git_status`、`git_diff`）
- [ ] 旧的 `src/codeagent/tools.py` 不再承载具体工具实现。（验证：文件不存在，或只保留兼容导出且测试覆盖导入路径）

## 工具行为

- [ ] `read_file` 返回带行号的内容。（验证：对临时文件运行工具，输出包含 `1\t...`）
- [ ] `write_file` 可以创建新文件。（验证：对不存在路径执行写入，文件内容符合预期）
- [ ] `write_file` 覆盖已有文件前要求先读取。（验证：未读取直接覆盖，返回 `is_error=True`）
- [ ] `edit_file` 要求 `old_string` 唯一。（验证：old_string 出现两次时返回错误）
- [ ] `edit_file` 编辑已有文件前要求先读取。（验证：未读取直接编辑，返回 `is_error=True`）
- [ ] `glob` 跳过 `.git`、`.venv`、`node_modules`、`__pycache__`。（验证：临时目录中创建这些目录，结果不包含其中路径）
- [ ] `grep` 支持正则搜索并返回 `file:line:content`。（验证：搜索临时文件中的唯一文本）
- [ ] `grep` 遇到非法正则返回工具错误，不抛 traceback。（验证：pattern 为 `[`）
- [ ] `bash` 捕获 stdout 和 stderr。（验证：执行同时输出 stdout/stderr 的命令）
- [ ] `bash` 非零退出码返回 `is_error=True`。（验证：执行 `exit 2`）
- [ ] `bash` 超时后返回错误并结束进程。（验证：执行超过 timeout 的 sleep 命令）
- [ ] `git_status` 返回当前工作区状态。（验证：在 git 仓库临时改文件后输出包含该文件）
- [ ] `git_diff` 返回当前 diff。（验证：修改临时文件后输出包含变更片段）

## ReAct 集成

- [ ] ReAct prompt 中包含默认工具描述。（验证：fake LLM 捕获 messages，system prompt 包含工具名）
- [ ] ReAct 可以连续调用至少三个不同工具后完成回答。（验证：fake LLM 测试通过）
- [ ] 工具返回错误时，错误以 Observation 形式回填给模型。（验证：fake LLM 调用不存在路径或非法参数）
- [ ] 达到 max_steps 时返回可记录的失败状态。（验证：fake LLM 一直输出 Action，最终状态为 `max_steps_exceeded` 或抛出可捕获错误并写记录）

## Run Record

- [ ] 每次运行生成一个 JSON run record。（验证：运行 CLI 单次 prompt，查看 `.codeagent/runs/` 新文件）
- [ ] run record 包含 run id、时间、workspace、用户输入、状态、最终回答。（验证：读取 JSON 字段）
- [ ] run record 的每个 step 包含模型输出、工具名、工具参数、Observation、错误状态。（验证：fake LLM 调用工具后读取 JSON）
- [ ] 失败运行也会记录错误原因。（验证：模拟 LLMError 或 max_steps exceeded）
- [ ] run record 不包含 API key 明文。（验证：grep 记录目录，不出现配置中的 key）
- [ ] CLI 交互模式在回答后显示 run record 路径。（验证：运行 `./run.sh` 提问，观察输出）

## Git Ignore

- [ ] `.codeagent/` 仍然被 git 忽略。（验证：`git status --ignored --short` 显示 `.codeagent/` 为 ignored）
- [ ] `.codeagent/runs/` 生成记录后不会进入待提交列表。（验证：生成记录后运行 `git status --short`）

## 编译与测试

- [ ] 所有单元测试通过。（验证：`PYTHONPATH=src python3 -m unittest discover -s tests`）
- [ ] 测试不依赖真实 DeepSeek 网络调用。（验证：测试中使用 fake LLM 或 fake client）
- [ ] CLI help 正常输出。（验证：`PYTHONPATH=src python3 -m codeagent.cli --help`）

## 端到端场景

- [ ] 场景 1：用户要求“查看当前项目有哪些 Python 文件并总结”，模型调用 `glob`、`read_file`、`grep` 后输出总结，并生成 run record。（验证：fake LLM 或受控集成测试）
- [ ] 场景 2：用户要求修改一个已存在文件，模型未先读取就调用 `edit_file`，工具拒绝；模型随后调用 `read_file` 再调用 `edit_file`，最终成功。（验证：fake LLM 测试）
- [ ] 场景 3：用户要求运行一个会失败的命令，`bash` 返回 stderr 和非零退出码，Agent 把错误纳入最终回答并生成失败/完成记录。（验证：fake LLM 测试）
