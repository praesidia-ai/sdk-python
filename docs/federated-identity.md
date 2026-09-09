# Federated credentials

`IdentityClient` exchanges fresh, administrator-bound external OIDC JWT assertions for short-lived Praesidia resource credentials. No management API key is needed for exchange. An assertion is one-use, requires issuer/subject/audience/issued-at/expiry claims, and may last at most one hour; the derived credential lasts at most five minutes. Production exchange endpoints require HTTPS. Redirects and automatic exchange retries are disabled.

```python
from praesidia.identity import IdentityClient

with IdentityClient("https://api.example.com") as identity:
    credential = identity.exchange(
        organization_id=org_id,
        subject_binding_id=workload_binding_id,
        subject_token=obtain_fresh_oidc_assertion(),
        resource="https://api.example.com/a2a/v1/agents/" + target_agent_id,
        scopes=["a2a:message", "a2a:task"],
    )
```

A delegated API exchange uses the user's external binding/assertion plus `actor_binding_id`, `actor_token`, and `consent_id`. The user must approve that exact actor, resources and scopes from their own browser session. The API retains the user's live membership, role, permission, feature and scope checks. Bare workload credentials cannot access user management APIs.

```python
with IdentityClient("https://api.example.com") as identity:
    acquire = identity.credential_provider(lambda: {
        "organization_id": org_id,
        "subject_binding_id": user_binding_id,
        "subject_token": obtain_fresh_user_assertion(),
        "actor_binding_id": workload_binding_id,
        "actor_token": obtain_fresh_workload_assertion(),
        "consent_id": consent_id,
        "resource": "https://api.example.com",
        "scopes": ["agents:invoke"],
    })
    client.refresh_credential(acquire())
    # Execute the authorized operation immediately after acquisition.
```

`down_exchange(token, subject_resource=..., resource=..., scopes=None)` binds the parent resource exactly and retains its scope set by default. The target must already be allowed by both the binding and consent. A repeated exchange returns the same child without extending expiry. `introspect(api_token)` performs a live API-audience check with no positive cache. Acquire a fresh assertion after an ambiguous external exchange failure; replaying its old JWT is rejected.

Revoking consent, a grant, provider trust, or a subject binding denies later use and durably cancels active linked tasks. Worker dispatch, poll delivery and task capability use check live authority. Already admitted remote calls may finish before revocation commits; cancellation does not assert remote reversal. Account erasure covers former memberships and removes external user subject strings. Operators must apply the identity migration, enable the federated identity module, configure canonical API/MCP resource URLs, and pin their real issuer's public verification keys and subject bindings.

Sources: [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693.html), [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html), [JWT BCP](https://www.rfc-editor.org/rfc/rfc8725.html), [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).
