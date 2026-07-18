from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from codeagent.prompts import GenerationUsage


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunStep:
    index: int
    llm_output: str
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
    observation: str | None = None
    is_error: bool = False
    generation_usage: GenerationUsage | None = None
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None


@dataclass
class RunRecord:
    run_id: str
    started_at: str
    ended_at: str | None
    status: str
    workspace: str
    user_input: str
    final_answer: str | None
    steps: list[RunStep] = field(default_factory=list)
    error: str | None = None


class RunRecorder:
    def __init__(
        self, workspace: str | Path, records_dir: str | Path | None = None
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.records_dir = (
            Path(records_dir) if records_dir else self.workspace / ".codeagent" / "runs"
        )
        self.record = RunRecord(
            run_id=uuid.uuid4().hex[:12],
            started_at=utc_now(),
            ended_at=None,
            status="running",
            workspace=str(self.workspace),
            user_input="",
            final_answer=None,
        )
        self.path: Path | None = None

    def start(self, user_input: str) -> None:
        self.record.user_input = user_input

    def record_step(
        self,
        *,
        llm_output: str,
        tool_name: str | None = None,
        tool_input: dict[str, object] | None = None,
        observation: str | None = None,
        is_error: bool = False,
        generation_usage: GenerationUsage | None = None,
        started_at: str | None = None,
    ) -> None:
        self.record.steps.append(
            RunStep(
                index=len(self.record.steps),
                llm_output=llm_output,
                tool_name=tool_name,
                tool_input=tool_input,
                observation=observation,
                is_error=is_error,
                generation_usage=generation_usage,
                started_at=started_at or utc_now(),
                ended_at=utc_now(),
            )
        )

    def complete(self, final_answer: str) -> None:
        self.record.status = "completed"
        self.record.final_answer = final_answer
        self.record.ended_at = utc_now()

    def fail(self, error: str, status: str = "failed") -> None:
        self.record.status = status
        self.record.error = error
        self.record.ended_at = utc_now()

    def save(self) -> Path:
        self.records_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.records_dir / f"{stamp}-{self.record.run_id}.json"
        path.write_text(
            json.dumps(asdict(self.record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.path = path
        return path
