from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeagent.permissions.dangerous import DangerousCommandDetector, is_safe_bash_command
from codeagent.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from codeagent.permissions.rules import ApprovalScope, Rule, RuleEngine, default_rules, extract_content
from codeagent.permissions.sandbox import PathSandbox
from codeagent.tools.base import Tool, display_path

_PLAN_ALLOWED_TOOLS = {"read_file", "find_file", "grep", "glob", "git_status", "git_diff"}


@dataclass(frozen=True)
class Decision:
    effect: DecisionEffect
    reason: str
    content: str = ""
    normalized_content: str = ""


class PermissionChecker:
    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode

    @classmethod
    def for_workspace(
        cls,
        workspace_root: str | Path,
        *,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> "PermissionChecker":
        root = Path(workspace_root).resolve()
        codeagent_dir = root / ".codeagent"
        return cls(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(root),
            rule_engine=RuleEngine(
                user_rules_path=Path.home() / ".codeagent" / "permissions.yaml",
                project_rules_path=codeagent_dir / "permissions.yaml",
                local_rules_path=codeagent_dir / "permissions.local.yaml",
                built_in_rules=default_rules(),
            ),
            mode=mode,
        )

    def check(self, tool: Tool, arguments: dict[str, object]) -> Decision:
        content = extract_content(tool.name, arguments)
        normalized = self._normalized_content(tool.name, content)

        if self.mode == PermissionMode.PLAN and tool.name in _PLAN_ALLOWED_TOOLS:
            return Decision("allow", "plan mode read-only tool", content, normalized)

        if tool.category == "command":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision("deny", f"dangerous command blocked: {reason}", content, normalized)
            if tool.name == "bash" and is_safe_bash_command(content):
                return Decision("allow", "safe read-only bash command", content, normalized)

        if tool.category in {"read", "write"} and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision("deny", f"path sandbox blocked: {reason}", content, normalized)

        rule_result = self.rule_engine.evaluate(tool.name, content, normalized)
        if rule_result is not None:
            return Decision(rule_result, f"permission rule {rule_result}", content, normalized)

        effect = mode_decide(self.mode, tool.category)
        return Decision(effect, f"permission mode {self.mode.value} {effect}", content, normalized)

    def approve(self, tool_name: str, pattern: str, scope: ApprovalScope) -> None:
        rule = Rule(tool_name=tool_name, pattern=pattern, effect="allow")
        if scope == "session":
            self.rule_engine.append_session_rule(rule)
        elif scope == "permanent":
            self.rule_engine.append_local_rule(rule)

    def _normalized_content(self, tool_name: str, content: str) -> str:
        if not content or tool_name not in {"read_file", "write_file", "edit_file", "git_diff"}:
            return content.strip() if tool_name == "bash" else content
        path = Path(content).expanduser()
        if not path.is_absolute():
            path = self.sandbox.project_root / path
        try:
            return display_path(self.sandbox.project_root, path.resolve(strict=False))
        except OSError:
            return content
