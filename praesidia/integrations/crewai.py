"""Explicit CrewAI managed tool with a host-owned durable Flow/Task cursor."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from .protected_tool import ManagedProtectedTool, RuntimeBinding


def _never_cache(*_):
    return False


def crewai_tool(managed: ManagedProtectedTool, *, binding: Callable[[], RuntimeBinding]):
    """Return a BaseTool. CrewAI does not inject a stable call ID into _run.

    The host must derive the binding from actual persisted Flow/Task state and a
    stable logical step ID. Do not expose that cursor as model arguments. This
    registered tool never executes unrelated builtin tools and installs no global
    fail-open hook. Persist pending state, approve externally, then re-enter the
    same tool/step; a new effect requires a new host-issued step ID.
    """
    from crewai.tools import BaseTool
    from pydantic import BaseModel, ConfigDict

    class Body(BaseModel):
        model_config = ConfigDict(extra="forbid")
        body: dict[str, Any]

    class ManagedCrewTool(BaseTool):
        def _run(self, body: dict[str, Any]) -> dict[str, Any]:
            actual = binding()
            if actual.call.runtime != "crewai":
                raise ValueError("CrewAI binding runtime mismatch")
            return managed.invoke(body, actual)

        async def _arun(self, body: dict[str, Any]) -> dict[str, Any]:
            return await asyncio.to_thread(self._run, body)

    return ManagedCrewTool(name=managed.name, description=managed.description,
                           args_schema=Body, cache_function=_never_cache)
