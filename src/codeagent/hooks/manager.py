from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from fnmatch import fnmatch
import json
from pathlib import Path
import re
import subprocess
from typing import Literal

import httpx
import yaml

from codeagent.permissions.dangerous import DangerousCommandDetector
from codeagent.permissions.rules import extract_content


HookSource = Literal["user", "shared", "local"]
HookStatus = Literal["ok", "blocked", "failed", "started", "skipped"]
ActionType = Literal["command", "prompt", "http", "subagent"]

_TOOL_PATTERN_RE = re.compile(r"^([A-Za-z_][\w-]*)\((.*)\)$")
_TRUSTED_SOURCES = {"user", "local"}


@dataclass(frozen=True)
class HookWarning:
    path: Path
    message: str


@dataclass(frozen=True)
class HookResult:
    rule_id: str
    event: str
    action_type: str
    status: HookStatus
    output: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HookEventContext:
    event: str
    workspace: Path
    planning_mode: bool = False
    model: str = "unknown"
    cwd: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
    tool_subject: str = ""

    def payload(self) -> dict[str, object]:
        data: dict[str, object] = {
            "event": self.event,
            "workspace": str(self.workspace),
            "planning_mode": self.planning_mode,
        }
        if self.model:
            data["model"] = self.model
        data["cwd"] = self.cwd or str(self.workspace)
        if self.tool_name is not None:
            data["tool_name"] = self.tool_name
            data["tool_input"] = self.tool_input or {}
            data["tool_subject"] = self.tool_subject
        return data


@dataclass(frozen=True)
class _HookAction:
    type: ActionType
    prompt: str = ""
    argv: tuple[str, ...] = ()
    method: str = "POST"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int | None = None
    agent: str = ""


@dataclass(frozen=True)
class _HookRule:
    rule_id: str
    event: str
    condition: object | None
    action: _HookAction
    source: HookSource
    path: Path
    once: bool = False
    background: bool = False
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class HookBeforeDecision:
    blocked: bool
    reason: str = ""
    results: tuple[HookResult, ...] = ()


class HookManager:
    def __init__(
        self, rules: list[_HookRule] | None = None, warnings: list[HookWarning] | None = None
    ) -> None:
        self.rules = rules or []
        self.warnings = warnings or []
        self._ran_once: set[str] = set()
        self._detector = DangerousCommandDetector()

    @classmethod
    def load_for_workspace(
        cls, workspace: str | Path, *, include_user_hooks: bool = True
    ) -> "HookManager":
        root = Path(workspace).resolve()
        specs: list[tuple[Path, HookSource]] = []
        if include_user_hooks:
            specs.append((Path.home() / ".codeagent" / "hooks.yaml", "user"))
        specs.extend(
            [
                (root / ".codeagent" / "hooks.yaml", "shared"),
                (root / ".codeagent" / "hooks.local.yaml", "local"),
            ]
        )
        rules: list[_HookRule] = []
        warnings: list[HookWarning] = []
        for path, source in specs:
            rules.extend(_load_file(path, source, warnings))
        return cls(rules, warnings)

    async def collect_reminders(self, context: HookEventContext) -> tuple[list[str], list[HookResult]]:
        reminders: list[str] = []
        results: list[HookResult] = []
        for rule in self._matching_rules(context):
            if rule.action.type != "prompt":
                result = await self._run_or_start(rule, context)
                results.append(result)
                continue
            if rule.event == "message.before_model":
                reminder = _format_hook_reminder(rule)
                reminders.append(reminder)
                result = HookResult(rule.rule_id, rule.event, "prompt", "ok", reminder)
                self._mark_once(rule)
                results.append(result)
        return reminders, results

    async def fire(self, context: HookEventContext) -> list[HookResult]:
        """对所有匹配当前事件的 hook 规则逐一触发。

        这是通用事件入口（turn.end、message.before_model 等），用于"通知型"hook：
        - prompt 类型的 hook：仅收集 prompt 文本放入结果，不阻塞主流程。
        - command/http/subagent 类型：通过 _run_or_start 真正执行外部动作。

        返回所有匹配规则的 HookResult 列表，由调用方自行决定如何使用。
        """
        results: list[HookResult] = []
        for rule in self._matching_rules(context):
            if rule.action.type == "prompt":
                # prompt hook 只附加一段文本到上下文中，永远不会阻塞
                result = HookResult(rule.rule_id, rule.event, "prompt", "ok", rule.action.prompt)
                self._mark_once(rule)
            else:
                result = await self._run_or_start(rule, context)
            results.append(result)
        return results

    async def fire_tool_before(self, context: HookEventContext) -> HookBeforeDecision:
        """在工具执行前触发 hook——用于安全拦截（tool.*.before 事件）。

        与 fire() 的关键区别：
        1. prompt 类型的 hook 此处语义为"阻塞工具调用"（而非仅通知）；
        2. 遇到第一个 prompt hook 立即短路返回，不再检查后续规则；
        3. 返回值是 HookBeforeDecision，包含 blocked 标志和拦截原因。

        只有当所有匹配规则执行完且无 prompt hook 拦截时，工具才会被放行。
        """
        results: list[HookResult] = []
        for rule in self._matching_rules(context):
            if rule.action.type == "prompt":
                # prompt hook 在工具执行前 = 安全闸门：直接阻塞工具调用
                result = HookResult(rule.rule_id, rule.event, "prompt", "blocked", rule.action.prompt)
                self._mark_once(rule)
                results.append(result)
                return HookBeforeDecision(True, rule.action.prompt, tuple(results))
            result = await self._run_or_start(rule, context)
            results.append(result)
        return HookBeforeDecision(False, results=tuple(results))

    def _matching_rules(self, context: HookEventContext) -> list[_HookRule]:
        matched: list[_HookRule] = []
        for rule in self.rules:
            if rule.event != context.event:
                continue
            if rule.once and rule.rule_id in self._ran_once:
                continue
            if _matches_condition(rule.condition, context):
                matched.append(rule)
        return matched

    async def _run_or_start(self, rule: _HookRule, context: HookEventContext) -> HookResult:
        if rule.background:
            asyncio.create_task(self._execute(rule, context))
            self._mark_once(rule)
            return HookResult(
                rule.rule_id,
                rule.event,
                rule.action.type,
                "started",
                metadata={"background": True},
            )
        result = await self._execute(rule, context)
        self._mark_once(rule)
        return result

    async def _execute(self, rule: _HookRule, context: HookEventContext) -> HookResult:
        try:
            if rule.action.type == "command":
                return await self._execute_command(rule, context)
            if rule.action.type == "http":
                return await self._execute_http(rule, context)
            if rule.action.type == "subagent":
                return HookResult(
                    rule.rule_id,
                    rule.event,
                    "subagent",
                    "skipped",
                    f"Subagent action '{rule.action.agent}' is not implemented yet.",
                )
            return HookResult(rule.rule_id, rule.event, rule.action.type, "ok", rule.action.prompt)
        except Exception as exc:  # noqa: BLE001 - hook failures never break the agent.
            return HookResult(
                rule.rule_id,
                rule.event,
                rule.action.type,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )

    async def _execute_command(self, rule: _HookRule, context: HookEventContext) -> HookResult:
        command_text = " ".join(rule.action.argv)
        hit, reason = self._detector.detect(command_text)
        if hit:
            return HookResult(
                rule.rule_id,
                rule.event,
                "command",
                "failed",
                f"dangerous command blocked: {reason}",
            )
        timeout = _effective_timeout(rule)
        completed = await asyncio.to_thread(
            subprocess.run,
            list(rule.action.argv),
            cwd=context.workspace,
            input=json.dumps(context.payload(), ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        parts: list[str] = []
        if completed.stdout:
            parts.append(f"STDOUT:\n{completed.stdout}")
        if completed.stderr:
            parts.append(f"STDERR:\n{completed.stderr}")
        output = "\n".join(parts) if parts else "(no output)"
        return HookResult(
            rule.rule_id,
            rule.event,
            "command",
            "ok" if completed.returncode == 0 else "failed",
            output[:4000],
            {"returncode": completed.returncode},
        )

    async def _execute_http(self, rule: _HookRule, context: HookEventContext) -> HookResult:
        timeout = _effective_timeout(rule)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                rule.action.method,
                rule.action.url,
                json=context.payload(),
                headers=rule.action.headers,
            )
        return HookResult(
            rule.rule_id,
            rule.event,
            "http",
            "ok" if 200 <= response.status_code < 300 else "failed",
            response.text[:4000],
            {"status_code": response.status_code},
        )

    def _mark_once(self, rule: _HookRule) -> None:
        if rule.once:
            self._ran_once.add(rule.rule_id)


def _load_file(path: Path, source: HookSource, warnings: list[HookWarning]) -> list[_HookRule]:
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        warnings.append(HookWarning(path, f"failed to parse hooks file: {exc}"))
        return []
    if raw is None:
        return []
    if not isinstance(raw, dict):
        warnings.append(HookWarning(path, "hooks file must contain a mapping"))
        return []
    entries = raw.get("hooks")
    if not isinstance(entries, list):
        warnings.append(HookWarning(path, "hooks file must contain a hooks list"))
        return []
    rules: list[_HookRule] = []
    for index, entry in enumerate(entries):
        rule = _parse_rule(entry, source, path, index, warnings)
        if rule is not None:
            rules.append(rule)
    return rules


def _parse_rule(
    entry: object,
    source: HookSource,
    path: Path,
    index: int,
    warnings: list[HookWarning],
) -> _HookRule | None:
    label = f"hook #{index + 1}"
    if not isinstance(entry, dict):
        warnings.append(HookWarning(path, f"{label} must be a mapping"))
        return None
    event = entry.get("event")
    if not isinstance(event, str) or not event:
        warnings.append(HookWarning(path, f"{label} has invalid event"))
        return None
    action = _parse_action(entry.get("action"), source, path, label, warnings)
    if action is None:
        return None
    raw_action = entry.get("action")
    action_background = raw_action.get("background") if isinstance(raw_action, dict) else None
    background = _optional_bool(entry.get("background", action_background), False)
    if background and event != "turn.end":
        warnings.append(HookWarning(path, f"{label} background is only allowed on turn.end in v1"))
        return None
    timeout_seconds = _optional_positive_int(entry.get("timeout_seconds"), path, f"{label}.timeout_seconds", warnings)
    if "if" in entry and not _valid_condition(entry["if"], event, path, label, warnings):
        return None
    return _HookRule(
        rule_id=f"{path}:{index + 1}",
        event=event,
        condition=entry.get("if"),
        action=action,
        source=source,
        path=path,
        once=_optional_bool(entry.get("once"), False),
        background=background,
        timeout_seconds=timeout_seconds,
    )


def _parse_action(
    raw: object,
    source: HookSource,
    path: Path,
    label: str,
    warnings: list[HookWarning],
) -> _HookAction | None:
    if not isinstance(raw, dict):
        warnings.append(HookWarning(path, f"{label}.action must be a mapping"))
        return None
    action_type = raw.get("type")
    if action_type not in {"command", "prompt", "http", "subagent"}:
        warnings.append(HookWarning(path, f"{label}.action.type is invalid"))
        return None
    if action_type in {"command", "http"} and source not in _TRUSTED_SOURCES:
        warnings.append(HookWarning(path, f"{label}.action {action_type} is disabled in shared hooks"))
        return None
    timeout_seconds = _optional_positive_int(raw.get("timeout_seconds"), path, f"{label}.action.timeout_seconds", warnings)
    if action_type == "command":
        if "command" in raw:
            warnings.append(HookWarning(path, f"{label}.action.command is not supported; use argv"))
            return None
        argv = raw.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(part, str) and part for part in argv)
        ):
            warnings.append(HookWarning(path, f"{label}.action.argv must be a non-empty string list"))
            return None
        return _HookAction("command", argv=tuple(argv), timeout_seconds=timeout_seconds)
    if action_type == "prompt":
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            warnings.append(HookWarning(path, f"{label}.action.prompt must be a non-empty string"))
            return None
        return _HookAction("prompt", prompt=prompt)
    if action_type == "http":
        method = raw.get("method", "POST")
        url = raw.get("url")
        if not isinstance(method, str) or method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            warnings.append(HookWarning(path, f"{label}.action.method is invalid"))
            return None
        if not isinstance(url, str) or not url.startswith("https://"):
            warnings.append(HookWarning(path, f"{label}.action.url must start with https://"))
            return None
        headers = raw.get("headers", {})
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            warnings.append(HookWarning(path, f"{label}.action.headers must be a string mapping"))
            return None
        return _HookAction("http", method=method.upper(), url=url, headers=headers, timeout_seconds=timeout_seconds)
    agent = raw.get("agent")
    if not isinstance(agent, str) or not agent:
        warnings.append(HookWarning(path, f"{label}.action.agent must be a non-empty string"))
        return None
    prompt = raw.get("prompt", "")
    if prompt and not isinstance(prompt, str):
        warnings.append(HookWarning(path, f"{label}.action.prompt must be a string"))
        return None
    return _HookAction("subagent", agent=agent, prompt=prompt)


def _valid_condition(
    raw: object,
    event: str,
    path: Path,
    label: str,
    warnings: list[HookWarning],
) -> bool:
    if not isinstance(raw, dict):
        warnings.append(HookWarning(path, f"{label}.if must be a mapping"))
        return False
    keys = set(raw)
    allowed = {"rule", "not", "regex", "glob", "exact", "all", "any", "planning_mode", "model", "cwd"}
    if not keys <= allowed:
        warnings.append(HookWarning(path, f"{label}.if has unknown fields"))
        return False
    if "all" in raw and "any" in raw:
        warnings.append(HookWarning(path, f"{label}.if cannot mix all and any"))
        return False
    if event.startswith("tool."):
        return True
    tool_keys = {"rule", "not", "regex", "glob", "exact", "all", "any"}
    if keys & tool_keys:
        warnings.append(HookWarning(path, f"{label}.if cannot use tool matchers on non-tool events"))
        return False
    return True


def _matches_condition(raw: object | None, context: HookEventContext) -> bool:
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return False
    if "all" in raw:
        items = raw["all"]
        return isinstance(items, list) and bool(items) and all(_matches_condition(item, context) for item in items)
    if "any" in raw:
        items = raw["any"]
        return isinstance(items, list) and bool(items) and any(_matches_condition(item, context) for item in items)
    for key in ("rule", "glob", "regex", "exact", "not"):
        if key in raw:
            value = raw[key]
            if not isinstance(value, str):
                return False
            matched = _matches_tool_pattern(key, value, context)
            return not matched if key == "not" else matched
    if "planning_mode" in raw and raw["planning_mode"] != context.planning_mode:
        return False
    if "model" in raw and not fnmatch(context.model, str(raw["model"])):
        return False
    if "cwd" in raw and not fnmatch(context.cwd or str(context.workspace), str(raw["cwd"])):
        return False
    return True


def _matches_tool_pattern(kind: str, raw: str, context: HookEventContext) -> bool:
    if context.tool_name is None:
        return False
    match = _TOOL_PATTERN_RE.match(raw.strip())
    if not match:
        return False
    tool_name, pattern = match.group(1), match.group(2)
    if tool_name != context.tool_name:
        return False
    subject = context.tool_subject
    if kind in {"rule", "glob", "not"}:
        return fnmatch(subject, pattern)
    if kind == "regex":
        return re.search(pattern, subject) is not None
    if kind == "exact":
        return subject == pattern
    return False


def _optional_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _optional_positive_int(
    value: object,
    path: Path,
    label: str,
    warnings: list[HookWarning],
) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        warnings.append(HookWarning(path, f"{label} must be a positive integer"))
        return None
    return value


def _effective_timeout(rule: _HookRule) -> int:
    return rule.action.timeout_seconds or rule.timeout_seconds or 60


def tool_subject(tool_name: str, arguments: dict[str, object]) -> str:
    return extract_content(tool_name, arguments)


def _format_hook_reminder(rule: _HookRule) -> str:
    return (
        "<hook-reminder "
        f"event=\"{rule.event}\" "
        f"source=\"{rule.source}\" "
        f"rule_id=\"{html_escape(rule.rule_id)}\">\n"
        "This is internal automation context injected by CodeAgent hooks. "
        "Use it to adjust behavior, but do not quote, summarize, acknowledge, "
        "or present it as a tool result or user request.\n\n"
        f"{rule.action.prompt.strip()}\n"
        "</hook-reminder>"
    )


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
