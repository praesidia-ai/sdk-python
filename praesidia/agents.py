"""
Praesidia SDK — AgentsResource.

Covers the ``/organizations/{org_id}/agents`` and
``/organizations/{org_id}/tasks`` management endpoints.
"""

from __future__ import annotations

from typing import Any

from ._http import (
    AGENT_ID_HEADER,
    CAPABILITY_TOKEN_HEADER,
    CHAIN_ID_HEADER,
    TASK_ID_HEADER,
    HttpClient,
)
from .exceptions import ForbiddenError


def tool_call_headers_from_task(task: dict[str, Any]) -> dict[str, str]:
    """
    Q3-02 / Q4-02 — build the ``X-Praesidia-*`` forwarding headers for a
    task-scoped MCP tool call from a polled task row.

    The four task-binding fields (``capabilityToken``, ``taskId``/``id``,
    ``agentId``/``serverAgentId``, ``chainId``) bind the tool call to the live
    task so the backend's use-time capability-token gate can fail-closed verify
    scope + expiry. The capability token is an opaque bearer secret — it is
    copied straight into the header and must never be logged.

    Absent fields are simply omitted (e.g. a task with no minted capability
    token yields no ``X-Praesidia-Capability-Token`` header).
    """
    headers: dict[str, str] = {}
    token = task.get("capabilityToken")
    if token:
        headers[CAPABILITY_TOKEN_HEADER] = token
    task_id = task.get("taskId") or task.get("id")
    if task_id:
        headers[TASK_ID_HEADER] = task_id
    agent_id = task.get("agentId") or task.get("serverAgentId")
    if agent_id:
        headers[AGENT_ID_HEADER] = agent_id
    chain_id = task.get("chainId")
    if chain_id:
        headers[CHAIN_ID_HEADER] = chain_id
    return headers


class AgentsResource:
    """
    Manage AI agents within an organisation.

    Endpoint base: ``/organizations/{org_id}/agents``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/agents"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
        """
        Return a paginated list of agents for the organisation.

        Args:
            page:  1-based page number (default: 1).
            limit: Maximum results per page (default: 20).

        Returns:
            A list of agent dicts as returned by the API.
        """
        result = self._http.get(self._base, params={"page": page, "limit": limit})
        # The API may return a pagination envelope or a plain list.
        if isinstance(result, list):
            return result
        return result.get("data", result.get("agents", []))

    def get(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch a single agent by ID.

        Args:
            agent_id: UUID of the agent to retrieve.

        Returns:
            Agent dict.

        Raises:
            NotFoundError: If no agent with that ID exists in the org.
        """
        return self._http.get(f"{self._base}/{agent_id}")

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new agent.

        Args:
            data: Agent creation payload (name, type, model, etc.).

        Returns:
            Created agent dict. Q4-05: the response carries
            ``credentialMode`` (``"jit"`` or ``"static"``) and
            ``clientSecret`` (``str`` or ``None``).

            - ``credentialMode == "jit"`` (the default for ephemeral/JIT-first
              orgs) → ``clientSecret`` is ``None``. Do NOT expect or persist a
              static ``X-A2A-Client-Secret``; the agent authenticates with
              ephemeral JIT capability tokens (Q4-02) minted per task instead.
            - ``credentialMode == "static"`` (legacy opt-in) → ``clientSecret``
              is the plaintext secret, shown ONCE. Persist it immediately.

            A ``clientId`` (public, non-secret) is ALWAYS returned regardless of
            mode. Never log ``clientSecret``.
        """
        return self._http.post(self._base, json=data)

    def update(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Partially update an agent.

        Args:
            agent_id: UUID of the agent to update.
            data:     Fields to update (only supplied fields are changed).

        Returns:
            Updated agent dict.
        """
        return self._http.patch(f"{self._base}/{agent_id}", json=data)

    def delete(self, agent_id: str) -> None:
        """
        Delete an agent.

        Args:
            agent_id: UUID of the agent to delete.
        """
        self._http.delete(f"{self._base}/{agent_id}")

    # ------------------------------------------------------------------
    # Credential rotation (Q4-01)
    # ------------------------------------------------------------------

    def rotate_client_secret(
        self,
        agent_id: str,
        grace_period_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Rotate an agent's A2A client secret, minting a fresh plaintext secret.

        Calls ``POST .../agents/{agent_id}/client-secret/rotate`` (requires the
        ``AGENTS_CONFIGURE`` permission).

        Pass ``grace_period_seconds`` (0..604800) to keep the OUTGOING secret
        valid for a bounded overlap so live consumers can swap over with zero
        downtime; omit it (or pass ``0``) for an instant, fail-closed rotation
        that revokes the old secret immediately (the emergency/panic path).
        Distinct from the instant ``regenerate-secret`` endpoint, which has no
        grace window.

        Args:
            agent_id:             UUID of the agent whose secret to rotate.
            grace_period_seconds: Overlap window in seconds (0..604800). Omit
                                  or ``0`` for an instant rotation. Clamped
                                  server-side.

        Returns:
            A dict with ``clientId``, ``clientSecret`` (the NEW plaintext
            secret), ``graceEndsAt`` (ISO-8601 str or None) and
            ``gracePeriodSeconds`` (effective, after clamping).

        Security:
            ``clientSecret`` is shown EXACTLY ONCE — Praesidia stores only its
            hash. Persist it immediately (it is never recoverable) and never
            log it.

        Raises:
            ForbiddenError: Q4-05 — JIT-first organizations have static client
                secrets disabled; the backend answers with HTTP 403. This is
                surfaced as a clear ``ForbiddenError`` (there is no static
                secret to rotate — the org uses ephemeral JIT capability tokens
                instead), never an unhandled crash.
        """
        payload: dict[str, Any] = {}
        if grace_period_seconds is not None:
            payload["gracePeriodSeconds"] = grace_period_seconds
        try:
            return self._http.post(
                f"{self._base}/{agent_id}/client-secret/rotate", json=payload
            )
        except ForbiddenError as exc:
            # Q4-05 — enrich the 403 with actionable context while preserving
            # the typed ForbiddenError so callers can still catch it narrowly.
            raise ForbiddenError(
                "Static client secrets are disabled for this organization "
                "(JIT-first). There is no static secret to rotate — this org "
                "authenticates with ephemeral JIT capability tokens (Q4-02). To "
                "re-enable legacy static secrets, an organization owner must turn "
                "on the `legacyStaticCredentials` setting (a deliberate security "
                f"downgrade). Server said: {exc.message}"
            ) from exc

    def refresh_credential(self, api_key: str) -> None:
        """
        Adopt a rotated credential in-process, at runtime (zero-downtime swap).

        Call this after :meth:`rotate_client_secret` with the returned
        ``clientSecret`` (or any newly provisioned credential): subsequent
        requests authenticate with the new secret. Combined with the grace
        window returned by :meth:`rotate_client_secret`, the previous secret
        keeps working until ``graceEndsAt``, so no in-flight caller is rejected
        during the swap. Also reachable as ``client.refresh_credential(...)``.

        Security: the credential is held only in memory and is never logged.
        """
        self._http.set_api_key(api_key)

    # ------------------------------------------------------------------
    # Task submission
    # ------------------------------------------------------------------

    def run(
        self,
        agent_id: str,
        input: dict[str, Any],
        dry_run: bool = False,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Submit a task to be executed by a specific agent.

        Posts to ``/organizations/{org_id}/tasks`` (the agent-tasks endpoint).

        Args:
            agent_id: UUID of the target agent.
            input:    Task input payload (e.g. ``{"message": "Hello, agent!"}``)
            dry_run:  When ``True`` the task is validated but not dispatched.
            chain_id: Q3-02 — chain-trace id to continue. Pass the id echoed
                      from an inbound ``X-Praesidia-Chain-Id`` header (or off a
                      polled task's ``chainId``) so this submit stays joined to
                      the same multi-agent chain. When set it is sent both as
                      the ``chainId`` body field and, via
                      :meth:`~praesidia.Praesidia.forward_chain`, forwarded on
                      subsequent outbound calls. Omit for a chain-root submit —
                      the server mints a fresh chainId. The SDK never mints one.

        Returns:
            Created task dict including ``id`` and ``status`` (plus ``chainId``
            and ``hopIndex`` once assigned).
        """
        payload: dict[str, Any] = {
            "agentId": agent_id,
            "input": input,
        }
        if dry_run:
            payload["dryRun"] = True
        if chain_id:
            payload["chainId"] = chain_id
            # Q3-02 — propagate the inbound chain on subsequent hops too.
            self._http.set_chain_id(chain_id)
        tasks_url = f"/organizations/{self._http.org_id}/tasks"
        return self._http.post(tasks_url, json=payload)

    # ------------------------------------------------------------------
    # Chain-aware polling + task-scoped MCP tool calls (Q3-02 / Q4-02)
    # ------------------------------------------------------------------

    def poll_pending_tasks(self, client_id: str) -> list[dict[str, Any]]:
        """
        Q3-02 / Q4-02 — claim the pending tasks routed to a polling (server)
        agent.

        Calls ``GET /a2a/tasks/pending/{client_id}``. Each returned task row now
        carries the chain identity (``chainId`` + ``hopIndex``) and, when
        governance is active, the short-lived JIT ``capabilityToken``
        (may be absent). Thread a claimed row straight into
        :meth:`call_mcp_tool` (or :func:`tool_call_headers_from_task`) so the
        chain + capability context is forwarded on downstream MCP hops.

        Args:
            client_id: The polling agent's A2A ``clientId``.

        Returns:
            A list of claimed task rows. NEVER log a row's ``capabilityToken``.
        """
        result = self._http.get(f"/a2a/tasks/pending/{client_id}")
        if isinstance(result, list):
            return result
        return result.get("data", result.get("tasks", []))

    def call_mcp_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task: dict[str, Any] | None = None,
        capability_token: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        chain_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """
        Q4-02 — execute an MCP tool call on behalf of a claimed task, carrying
        the four task-binding fields to the backend.

        Posts to
        ``/organizations/{org_id}/mcp-servers/{server_id}/tools/{tool_name}/call``
        and forwards ``capabilityToken``, ``taskId``, ``agentId`` and
        ``chainId`` as ``X-Praesidia-*`` request headers so the backend's
        use-time capability-token gate can bind the call to the live task and
        fail-closed verify scope + expiry.

        Pass a polled task row via ``task`` to lift all four fields at once
        (see :func:`tool_call_headers_from_task`); explicit keyword arguments
        override anything derived from ``task``.

        The capability token is opaque — it is forwarded only in the header,
        never in the request body, and must never be logged.

        Args:
            server_id:        UUID of the MCP server connection.
            tool_name:        Name of the tool to invoke.
            arguments:        Tool arguments object.
            task:             Optional polled task row to derive headers from.
            capability_token: Opaque JIT capability token (overrides ``task``).
            task_id:          Owning task id (overrides ``task``).
            agent_id:         Executing agent id (overrides ``task``).
            chain_id:         Chain-trace id (overrides ``task``).
            timeout_ms:       Optional per-call timeout in milliseconds.

        Returns:
            The tool result dict.
        """
        headers = tool_call_headers_from_task(task) if task else {}
        if capability_token:
            headers[CAPABILITY_TOKEN_HEADER] = capability_token
        if task_id:
            headers[TASK_ID_HEADER] = task_id
        if agent_id:
            headers[AGENT_ID_HEADER] = agent_id
        if chain_id:
            headers[CHAIN_ID_HEADER] = chain_id

        body: dict[str, Any] = {"arguments": arguments or {}}
        if timeout_ms is not None:
            body["timeoutMs"] = timeout_ms

        path = (
            f"/organizations/{self._http.org_id}"
            f"/mcp-servers/{server_id}/tools/{tool_name}/call"
        )
        return self._http.post(path, json=body, headers=headers or None)
