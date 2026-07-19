# CodeAgent

CodeAgent 是一个本地 code agent，把用户输入转成模型决策、工具调用和最终回答。

## Language

**Agent Turn（代理轮次）**:
CodeAgent 处理一次用户输入的完整过程，从初始提示开始，到最终回答或失败结束。一个代理轮次可以包含多次模型调用和多次工具调用。
_Avoid_: request, task, session

**Run Trace（运行追踪）**:
用于调试一个代理轮次的可观测故事。运行追踪把模型调用和工具调用放在同一个用户意图下，保留它们之间的因果关系。
_Avoid_: tool trace, call trace

**Generation（模型生成）**:
代理轮次中发生的一次模型调用。
_Avoid_: completion, LLM request

**Tool Call（工具调用）**:
代理轮次中对一个工作区工具的一次调用。
_Avoid_: action, command

**Run Record（运行记录）**:
一个代理轮次对应的本地 JSON 记录，用于离线复盘和调试。
_Avoid_: log file, transcript

**Prompt Bundle（提示包）**:
一次模型生成所需的提示上下文集合，包含稳定提示块、环境块、补充提醒和对话消息。
_Avoid_: system prompt string, prompt text

**Stable Prompt Block（稳定提示块）**:
跨轮保持逐字节一致、适合进入 provider 缓存的提示内容。它包含稳定系统指令和工具定义，不包含环境、历史或轮次相关内容。
_Avoid_: cached prompt, static prefix

**Environment Block（环境块）**:
描述当前运行环境的动态提示内容，例如工作目录、平台、日期、git 状态、应用版本和当前模型。
_Avoid_: env prompt, runtime info

**System Reminder（系统提醒）**:
代理轮次内动态注入的补充指令，用特殊标签标识为系统上下文，不写入持久历史。
_Avoid_: injected user message, reminder prompt

**Planning Mode（规划模式）**:
会话级模式，要求代理先制定计划并限制为只读工具，直到用户切回执行。
_Avoid_: plan prompt, readonly mode

**Planning Command（规划命令）**:
在 PCode TUI 中切换规划模式的本地控制输入，例如 `/plan` 和 `/do`。规划命令不是用户任务本身。
_Avoid_: slash prompt, plan message

**Read-Only Tool Surface（只读工具面）**:
规划模式下模型可见且可执行的工具集合，只包含不会修改工作区或运行任意命令的工具。
_Avoid_: safe tools, planning tools

**Agent Loop Step（代理循环步）**:
代理轮次中的一次模型生成尝试，可能产生工具调用、最终回答或格式错误反馈。
_Avoid_: iteration, retry

**Agent Session（代理会话）**:
一次连续的 CodeAgent 交互上下文，包含多次代理轮次和共享的会话状态。
_Avoid_: chat, conversation

**Lifecycle Event（生命周期事件）**:
Agent 生命周期中的一个可观察触发点，例如会话开始、轮次开始、模型消息产生、工具执行前或工具执行后。
_Avoid_: callback, trigger

**Hook Rule（Hook 规则）**:
声明式自动化规则，由生命周期事件、可选条件和固定动作组成。
_Avoid_: automation, script

**Hook Action（Hook 动作）**:
Hook 规则命中后执行的固定行为，例如运行命令、注入提示、发送 HTTP 请求或启动子 Agent。
_Avoid_: handler, callback

**Hook Condition（Hook 条件）**:
Hook 规则中用于筛选事件上下文的匹配表达式。省略时表示该规则无条件触发。
_Avoid_: matcher, filter

**Prompt Module（提示模块）**:
稳定系统指令中的一个有名称和优先级的职责片段。提示模块按优先级装配成稳定提示块。
_Avoid_: prompt section, instruction chunk

**Tool Definition（工具定义）**:
模型可见的工具能力说明和参数结构，用于指导模型选择和调用工作区工具。
_Avoid_: tool schema, function spec

**Generation Usage（模型用量）**:
一次模型生成返回的 token 用量信息，包括普通输入输出 token 和可选的缓存写入/读取 token。
_Avoid_: token stats, usage dict
