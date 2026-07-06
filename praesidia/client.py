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

from ._http import HttpClient
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
    ) -> None:
        self._http = HttpClient(api_key=api_key, org_id=org_id, base_url=base_url)
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
        Adopt a rotated credential in-process, at runtime (zero-downtime swap).

        After rotating an agent's client secret with a grace window (see
        :meth:`~praesidia.agents.AgentsResource.rotate_client_secret`), pass the
        returned ``clientSecret`` here so every subsequent request from this
        client — across all resources — authenticates with the new secret. The
        server-side grace overlap keeps the previous secret valid until
        ``graceEndsAt``, so in-flight callers are never rejected during the swap.

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
