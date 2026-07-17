from __future__ import annotations

from contextlib import nullcontext
import os
from typing import Any, Protocol


class Observation(Protocol):
    def update(self, **kwargs: object) -> None:
        ...


class ObservationContext(Protocol):
    def __enter__(self) -> Observation:
        ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
        ...


class Tracer(Protocol):
    def start_run(self, *, name: str, user_input: str, metadata: dict[str, object]) -> ObservationContext:
        ...

    def start_generation(
        self,
        *,
        name: str,
        model: str | None,
        input: object,
        metadata: dict[str, object],
    ) -> ObservationContext:
        ...

    def start_tool(self, *, name: str, input: object) -> ObservationContext:
        ...

    def flush(self) -> None:
        ...


class NoopTracer:
    def start_run(self, *, name: str, user_input: str, metadata: dict[str, object]) -> ObservationContext:
        return nullcontext(_NoopObservation())

    def start_generation(
        self,
        *,
        name: str,
        model: str | None,
        input: object,
        metadata: dict[str, object],
    ) -> ObservationContext:
        return nullcontext(_NoopObservation())

    def start_tool(self, *, name: str, input: object) -> ObservationContext:
        return nullcontext(_NoopObservation())

    def flush(self) -> None:
        return None


class LangfuseTracer:
    def __init__(self, client: Any) -> None:
        self.client = client

    def start_run(self, *, name: str, user_input: str, metadata: dict[str, object]) -> ObservationContext:
        try:
            context = self.client.start_as_current_observation(as_type="span", name=name)
        except Exception:  # noqa: BLE001 - observability must not break agent execution.
            return nullcontext(_NoopObservation())
        return _UpdatingContext(context, input=user_input, metadata=metadata)

    def start_generation(
        self,
        *,
        name: str,
        model: str | None,
        input: object,
        metadata: dict[str, object],
    ) -> ObservationContext:
        kwargs = {"as_type": "generation", "name": name}
        if model:
            kwargs["model"] = model
        try:
            context = self.client.start_as_current_observation(**kwargs)
        except Exception:  # noqa: BLE001 - observability must not break agent execution.
            return nullcontext(_NoopObservation())
        return _UpdatingContext(context, input=input, metadata=metadata)

    def start_tool(self, *, name: str, input: object) -> ObservationContext:
        try:
            context = self.client.start_as_current_observation(as_type="span", name=f"tool:{name}")
        except Exception:  # noqa: BLE001 - observability must not break agent execution.
            return nullcontext(_NoopObservation())
        return _UpdatingContext(context, input=input)

    def flush(self) -> None:
        try:
            self.client.flush()
        except Exception:  # noqa: BLE001 - observability must not break agent execution.
            return None


class _NoopObservation:
    def update(self, **kwargs: object) -> None:
        return None


class _UpdatingContext:
    def __init__(self, context: ObservationContext, **initial_update: object) -> None:
        self.context = context
        self.initial_update = {key: value for key, value in initial_update.items() if value is not None}
        self.observation: Observation | None = None

    def __enter__(self) -> Observation:
        self.observation = self.context.__enter__()
        if self.initial_update:
            try:
                self.observation.update(**self.initial_update)
            except Exception:  # noqa: BLE001 - observability must not break agent execution.
                return self.observation
        return self.observation

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
        return self.context.__exit__(exc_type, exc, traceback)


def build_tracer_from_env() -> Tracer:
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        return NoopTracer()
    try:
        from langfuse import get_client
    except ImportError:
        return NoopTracer()
    return LangfuseTracer(get_client())


def tracing_status_from_env() -> str:
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        return "trace: off"
    base_url = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST")
    return f"trace: langfuse {base_url}" if base_url else "trace: langfuse"
