"""
Praesidia SDK — AgentsResource.

Covers the ``/organizations/{org_id}/agents`` and
``/organizations/{org_id}/tasks`` management endpoints.
"""

from __future__ import annotations

import re
from typing import Any

from ._http import (
    AGENT_ID_HEADER,
    CAPABILITY_TOKEN_HEADER,
    CHAIN_ID_HEADER,
    TASK_ID_HEADER,
    HttpClient,
    path_segment,
)

#: AUDIT-SDK-02 — RFC-4122 UUID matcher. ``CreateAgentTaskDto.chainId`` is
#: ``@IsUUID``, so the SDK validates it client-side for a clear error.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


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
        return self._http.get(f"{self._base}/{path_segment(agent_id, 'agent_id')}")

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
        return self._http.patch(
            f"{self._base}/{path_segment(agent_id, 'agent_id')}", json=data
        )

    def delete(self, agent_id: str) -> None:
        """
        Delete an agent.

        Args:
            agent_id: UUID of the agent to delete.
        """
        self._http.delete(f"{self._base}/{path_segment(agent_id, 'agent_id')}")

    # ------------------------------------------------------------------
    # Credential refresh (Q4-01)
    # ------------------------------------------------------------------

    def refresh_credential(self, api_key: str) -> None:
        """
        Adopt a newly provisioned credential in-process, at runtime
        (zero-downtime swap).

        Call this with a freshly provisioned management API key: subsequent
        requests authenticate with the new key, so a
        long-lived client can swap credentials without recreating it or
        restarting the process. Also reachable as
        ``client.refresh_credential(...)``.

        Security: the credential is held only in memory and is never logged.
        """
        self._http.set_api_key(api_key)

    # ------------------------------------------------------------------
    # Task submission
    # ------------------------------------------------------------------

    #: AUDIT-SDK-02 — task types accepted by ``CreateAgentTaskDto.type``
    #: (mirrors the backend ``AgentTaskType`` enum).
    TASK_TYPES = ("MESSAGE", "TOOL_CALL", "DELEGATION")

    def run(
        self,
        connection_id: str,
        input: dict[str, Any],
        *,
        type: str = "MESSAGE",
        dry_run: bool = False,
        chain_id: str | None = None,
        callback_url: str | None = None,
        parent_task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Submit a task to an agent connection.

        Posts to ``/organizations/{org_id}/tasks``, which binds the backend
        ``CreateAgentTaskDto``. That DTO REQUIRES ``connectionId`` (a UUID),
        ``type`` (an :attr:`TASK_TYPES` value) and a non-empty ``input`` object.

        .. versionchanged:: AUDIT-SDK-02
            **Breaking:** the first positional argument is now ``connection_id``
            (was ``agent_id``); ``agentId`` is not a task field. ``type`` is a
            new keyword. The previous body (``{"agentId": …, "input": …}``)
            always 400'd against the real DTO.

        Args:
            connection_id: UUID of the agent-to-agent connection to route
                           through (``CreateAgentTaskDto.connectionId``).
            input:         Non-empty task input object, e.g.
                           ``{"message": "Hello, agent!"}``.
            type:          Task type — one of :attr:`TASK_TYPES`
                           (default ``"MESSAGE"``).
            dry_run:       When ``True`` the task runs in sandbox mode
                           (billing skipped).
            chain_id:      Q3-02 — chain-trace id (UUID) to continue. Pass the
                           id echoed from an inbound ``X-Praesidia-Chain-Id``
                           header (or off a polled task's ``chainId``) so this
                           submit stays joined to the same multi-agent chain.
                           When set it is sent as the ``chainId`` body field and
                           on this request's trace header only. Omit for a
                           chain-root submit — the server mints a fresh chainId.
                           Use ``client.forward_chain`` only when every request
                           from that client intentionally shares one chain. The
                           SDK never mints an id.
            callback_url:  Optional webhook URL for the task result.
            parent_task_id: Optional parent task UUID for a delegated sub-task.

        Returns:
            Created task dict including ``id`` and ``status`` (plus ``chainId``
            and ``hopIndex`` once assigned).

        Raises:
            ValueError: If ``input`` is not a non-empty dict or ``type`` is not
                        a valid :attr:`TASK_TYPES` value (surfaced client-side
                        instead of a raw backend 400).
        """
        if not isinstance(input, dict) or not input:
            raise ValueError(
                "input must be a non-empty dict (CreateAgentTaskDto.input is "
                "@IsObject @IsNotEmpty), e.g. {'message': 'Hello, agent!'}"
            )
        if not isinstance(connection_id, str) or not _UUID_RE.match(connection_id):
            raise ValueError("connection_id must be an RFC-4122 UUID")
        if type not in self.TASK_TYPES:
            raise ValueError(
                f"type must be one of {self.TASK_TYPES}; got {type!r}"
            )
        if chain_id is not None and not _UUID_RE.match(chain_id):
            raise ValueError(
                "chain_id must be a UUID (CreateAgentTaskDto.chainId is "
                f"@IsUUID); got {chain_id!r}"
            )
        if parent_task_id is not None and not _UUID_RE.match(parent_task_id):
            raise ValueError("parent_task_id must be an RFC-4122 UUID")
        payload: dict[str, Any] = {
            "connectionId": connection_id,
            "type": type,
            "input": input,
        }
        if dry_run:
            payload["dryRun"] = True
        if callback_url:
            payload["callbackUrl"] = callback_url
        if parent_task_id:
            payload["parentTaskId"] = parent_task_id
        if chain_id:
            payload["chainId"] = chain_id
        tasks_url = f"/organizations/{self._http.org_id}/tasks"
        headers = {CHAIN_ID_HEADER: chain_id} if chain_id else None
        return self._http.post(tasks_url, json=payload, headers=headers)

    # ------------------------------------------------------------------
    # Chain-aware polling + task-scoped MCP tool calls (Q3-02 / Q4-02)
    # ------------------------------------------------------------------

    def poll_pending_tasks(
        self,
        client_id: str,
        *,
        client_secret: str | None = None,
        access_token: str | None = None,
    ) -> list[dict[str, Any]]:
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
            client_secret: Static A2A client secret. Sends only the required
                           ``X-A2A-*`` headers (no management Bearer header).
            access_token: OAuth A2A access token. Mutually exclusive with
                          ``client_secret``. When both are omitted, the
                          client's configured Bearer credential is used.

        Returns:
            A list of claimed task rows. NEVER log a row's ``capabilityToken``.
        """
        if client_secret is not None and access_token is not None:
            raise ValueError("provide client_secret or access_token, not both")
        headers: dict[str, str] | None = None
        include_auth = True
        if client_secret is not None:
            if not client_secret or "\r" in client_secret or "\n" in client_secret:
                raise ValueError("client_secret must be a non-empty single-line string")
            headers = {
                "X-A2A-Client-Id": client_id,
                "X-A2A-Client-Secret": client_secret,
            }
            # A2A auth treats any Bearer header as authoritative, so the
            # management API key must be omitted for static-secret auth.
            include_auth = False
        elif access_token is not None:
            if not access_token or "\r" in access_token or "\n" in access_token:
                raise ValueError("access_token must be a non-empty single-line string")
            headers = {"Authorization": f"Bearer {access_token}"}
        result = self._http.get(
            f"/a2a/tasks/pending/{path_segment(client_id, 'client_id')}",
            headers=headers,
            include_auth=include_auth,
        )
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

        body: dict[str, Any] = {
            "toolName": tool_name,
            "arguments": arguments or {},
        }
        if timeout_ms is not None:
            if (
                isinstance(timeout_ms, bool)
                or not isinstance(timeout_ms, (int, float))
                or not 1000 <= timeout_ms <= 300_000
            ):
                raise ValueError("timeout_ms must be a number from 1000 to 300000")
            body["timeoutMs"] = timeout_ms

        path = (
            f"/organizations/{self._http.org_id}"
            f"/mcp-servers/{path_segment(server_id, 'server_id')}"
            f"/tools/{path_segment(tool_name, 'tool_name')}/call"
        )
        return self._http.post(path, json=body, headers=headers or None)
