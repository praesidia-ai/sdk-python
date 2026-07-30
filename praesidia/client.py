"""
Praesidia SDK — top-level client.

Usage::

    from praesidia import Praesidia

    client = Praesidia(
        api_key="sk-...",
        org_id="your-org-id",
        base_url="https://api.praesidia.ai",
    )

    agents = client.agents.list()
    task   = client.agents.run("agent-id", input={"message": "Hello!"})
"""

from __future__ import annotations

from typing import Union

from ._http import HttpClient
from ._retry import RetryConfig
from .agents import AgentsResource
from .analytics import AnalyticsResource
from .audit import AuditResource
from .compliance import ComplianceResource
from .connections import ConnectionsResource
from .memory import MemoryResource
from .telemetry import TelemetryResource
from .trust import TrustResource
from .workflows import WorkflowsResource

__all__ = ["Praesidia"]

_DEFAULT_BASE_URL = "https://api.praesidia.ai"


class Praesidia:
    """
    Entry point for the Praesidia management SDK.

    Args:
        api_key:  API key string (obtain via the Praesidia dashboard under
                  *Settings → API Keys*).
        org_id:   Organisation UUID.  Every resource call is scoped to this
                  organisation.
        base_url: Override the backend base URL.  Defaults to the hosted API
                  at ``https://api.praesidia.ai``.  Set to
                  ``http://localhost:5001`` for local development.
        timeout:   Per-operation HTTP timeout in seconds (default: 30, maximum:
                   300). Bulk downloads use their own finite idle timeout.
        retry:     FINDING-4 — bounded retry policy for GET/DELETE (and
                   idempotency-keyed POST/PATCH) requests. A ``RetryConfig``
                   instance, ``None`` (default policy: 3 attempts, jittered
                   backoff, 15s budget, honours ``Retry-After``), or ``False``
                   to disable retries entirely.

    Attributes:
        agents:      :class:`~praesidia.agents.AgentsResource`
        workflows:   :class:`~praesidia.workflows.WorkflowsResource`
        audit:       :class:`~praesidia.audit.AuditResource`
        analytics:   :class:`~praesidia.analytics.AnalyticsResource`
        connections: :class:`~praesidia.connections.ConnectionsResource`
        compliance:  :class:`~praesidia.compliance.ComplianceResource`
        memory:      :class:`~praesidia.memory.MemoryResource`
        telemetry:   :class:`~praesidia.telemetry.TelemetryResource`
        trust:       :class:`~praesidia.trust.TrustResource`

    Example::

        from praesidia import Praesidia

        client = Praesidia(api_key="sk-...", org_id="...")

        # List agents
        for agent in client.agents.list():
            print(agent["name"])

        # Run a task
        task = client.agents.run("agent-id", input={"message": "Summarise this"})
        print(task["status"])

        # Stream audit log
        for event in client.audit.stream(from_date="2026-01-01"):
            print(event["action"], event["createdAt"])
    """

    def __init__(
        self,
        api_key: str,
        org_id: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
        retry: Union[RetryConfig, bool, None] = None,
    ) -> None:
        self._http = HttpClient(
            api_key=api_key,
            org_id=org_id,
            base_url=base_url,
            timeout=timeout,
            retry=retry,
        )
        self.agents = AgentsResource(self._http)
        self.workflows = WorkflowsResource(self._http)
        self.audit = AuditResource(self._http)
        self.analytics = AnalyticsResource(self._http)
        self.connections = ConnectionsResource(self._http)
        self.compliance = ComplianceResource(self._http)
        self.memory = MemoryResource(self._http)
        self.telemetry = TelemetryResource(self._http)
        self.trust = TrustResource(self._http)

    def refresh_credential(self, api_key: str) -> None:
        """
        Adopt a newly provisioned credential in-process, at runtime
        (zero-downtime swap).

        Pass a freshly provisioned management API key here so every subsequent
        request from this client — across all resources — authenticates with
        the new key, without recreating the client. A2A client secrets belong
        only in ``agents.poll_pending_tasks(..., client_secret=...)``.

        Security: the credential is held only in memory and is never logged.
        """
        self._http.set_api_key(api_key)

    def forward_chain(self, chain_id: str | None) -> None:
        """
        Q3-02 — adopt an inbound chain-trace id and forward it (unchanged) on
        every subsequent outbound call from this client — across all resources
        — as the ``X-Praesidia-Chain-Id`` header. Call this with the id echoed
        from an inbound ``X-Praesidia-Chain-Id`` header so a multi-agent chain
        stays correlated across SDK-driven hops. Pass ``None`` to stop.

        The SDK NEVER mints a chainId — it only propagates one it received.
        """
        self._http.set_chain_id(chain_id)
