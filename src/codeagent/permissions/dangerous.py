from __future__ import annotations

import re


_DANGEROUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*\s+/\s*$"), "recursive forced root deletion"),
    (re.compile(r"\bmkfs\."), "disk formatting"),
    (re.compile(r"\bdd\s+[^;&|]*\bof=/dev/"), "direct block-device write"),
    (re.compile(r"\bchmod\s+-R\s+777\s+/"), "recursive root permission mutation"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"\bcurl\b.*\|\s*(?:ba)?sh\b"), "remote script piped to shell"),
    (re.compile(r"\bwget\b.*\|\s*(?:ba)?sh\b"), "remote script piped to shell"),
    (re.compile(r">\s*/dev/(?:sd|hd|nvme|disk)"), "direct overwrite of disk device"),
)

_SAFE_BASH_PREFIXES = (
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "grep",
    "git status",
    "git diff",
    "git log",
    "git show",
)

_SHELL_CONTROL_TOKENS = ("|", ";", "&&", "||", ">", "<", "`", "$(")


class DangerousCommandDetector:
    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex, reason in extra_patterns:
                self._patterns.append((re.compile(regex), reason))

    def detect(self, command: str) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""


def is_safe_bash_command(command: str) -> bool:
    trimmed = command.strip()
    if not trimmed:
        return False
    if any(token in trimmed for token in _SHELL_CONTROL_TOKENS):
        return False
    return any(trimmed == safe or trimmed.startswith(safe + " ") for safe in _SAFE_BASH_PREFIXES)
