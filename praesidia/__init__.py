"""
praesidia — Python management SDK for the Praesidia AI agent platform.

Quick start::

    from praesidia import Praesidia

    client = Praesidia(
        api_key="sk-...",
        org_id="your-org-id",
        base_url="http://localhost:5001",   # omit for hosted API
    )

    # List agents
    agents = client.agents.list()

    # Run an agent task
    task = client.agents.run("agent-id", input={"message": "Hello, agent!"})
    print(task["id"], task["status"])

    # Stream the audit log
    for event in client.audit.stream(from_date="2026-01-01"):
        print(event)
"""

from .agents import tool_call_headers_from_task
from .client import Praesidia
from .exceptions import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    PraesidiaError,
    RateLimitError,
    ServerError,
)

__all__ = [
    "Praesidia",
    "PraesidiaError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "tool_call_headers_from_task",
]

__version__ = "0.1.0"
