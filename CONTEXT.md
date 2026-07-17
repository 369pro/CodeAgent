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
