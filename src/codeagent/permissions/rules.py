from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
import re
from typing import Literal

import yaml

Effect = Literal["allow", "deny"]
ApprovalScope = Literal["once", "session", "permanent"]

_RULE_RE = re.compile(r"^([A-Za-z_][\w-]*)\((.*)\)$")
_CONTENT_FIELDS = {
    "bash": "command",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "grep": "pattern",
    "glob": "pattern",
    "find_file": "name",
    "git_diff": "path",
}


@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect

    def matches(self, tool_name: str, content: str, normalized_content: str = "") -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern) or (
            bool(normalized_content) and fnmatch(normalized_content, self.pattern)
        )


@dataclass
class RuleLoadWarning:
    path: Path
    message: str


@dataclass
class RuleEngine:
    user_rules_path: Path | None = None
    project_rules_path: Path | None = None
    local_rules_path: Path | None = None
    built_in_rules: list[Rule] = field(default_factory=list)
    session_rules: list[Rule] = field(default_factory=list)
    warnings: list[RuleLoadWarning] = field(default_factory=list)

    def evaluate(self, tool_name: str, content: str, normalized_content: str = "") -> Effect | None:
        for rules in self._tiers_high_to_low():
            for rule in reversed(rules):
                if rule.matches(tool_name, content, normalized_content):
                    return rule.effect
        return None

    def append_session_rule(self, rule: Rule) -> None:
        self.session_rules.append(rule)

    def append_local_rule(self, rule: Rule) -> None:
        if self.local_rules_path is None:
            return
        self.local_rules_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self.local_rules_path, self.warnings)
        existing.append(rule)
        entries = [{"rule": f"{r.tool_name}({r.pattern})", "effect": r.effect} for r in existing]
        self.local_rules_path.write_text(
            yaml.safe_dump(entries, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _tiers_high_to_low(self) -> list[list[Rule]]:
        return [
            self.session_rules,
            _load_rules_file(self.local_rules_path, self.warnings),
            _load_rules_file(self.project_rules_path, self.warnings),
            _load_rules_file(self.user_rules_path, self.warnings),
            self.built_in_rules,
        ]


def parse_rule(raw: str, effect: Effect) -> Rule:
    match = _RULE_RE.match(raw.strip())
    if not match:
        raise ValueError(f"invalid rule syntax: {raw}")
    return Rule(tool_name=match.group(1), pattern=match.group(2), effect=effect)


def extract_content(tool_name: str, arguments: dict[str, object]) -> str:
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    value = arguments.get(field, "")
    return value.strip() if tool_name == "bash" and isinstance(value, str) else str(value)


def default_rules() -> list[Rule]:
    return [
        Rule("read_file", ".env", "deny"),
        Rule("read_file", ".env.*", "deny"),
        Rule("read_file", "**/.env", "deny"),
        Rule("read_file", "**/.env.*", "deny"),
        Rule("read_file", ".env.example", "allow"),
        Rule("read_file", ".env.sample", "allow"),
        Rule("read_file", "**/.env.example", "allow"),
        Rule("read_file", "**/.env.sample", "allow"),
        Rule("read_file", ".git/config", "deny"),
        Rule("read_file", "**/.git/config", "deny"),
        Rule("write_file", ".git/**", "deny"),
        Rule("write_file", "**/.git/**", "deny"),
        Rule("edit_file", ".git/**", "deny"),
        Rule("edit_file", "**/.git/**", "deny"),
    ]


def _load_rules_file(path: Path | None, warnings: list[RuleLoadWarning]) -> list[Rule]:
    if path is None or not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        warnings.append(RuleLoadWarning(path, f"failed to parse rules file: {exc}"))
        return []
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(RuleLoadWarning(path, "rules file must contain a list"))
        return []
    rules: list[Rule] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(RuleLoadWarning(path, f"rule #{index + 1} must be a mapping"))
            continue
        effect = entry.get("effect")
        rule_raw = entry.get("rule")
        if effect not in ("allow", "deny") or not isinstance(rule_raw, str):
            warnings.append(RuleLoadWarning(path, f"rule #{index + 1} has invalid fields"))
            continue
        try:
            rules.append(parse_rule(rule_raw, effect))
        except ValueError as exc:
            warnings.append(RuleLoadWarning(path, str(exc)))
    return rules
