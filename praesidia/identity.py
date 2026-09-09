"""RFC 8693 identity exchange. Assertions are never retried or sent on redirects."""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


class IdentityError(RuntimeError):
    """A rejected identity operation; error text never contains credentials."""


class IdentityClient:
    def __init__(self, base_url: str = "https://api.praesidia.ai", *, timeout: float = 30.0) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or "\\" in base_url or any(char.isspace() for char in base_url):
            raise ValueError("Identity base_url must be an absolute URL without credentials, query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Identity assertions require HTTPS outside loopback development")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("Identity timeout must be between zero and 300 seconds")
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> IdentityClient:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def exchange(self, *, organization_id: str, subject_binding_id: str, subject_token: str, resource: str, scopes: list[str], actor_binding_id: str | None = None, actor_token: str | None = None, consent_id: str | None = None) -> dict[str, Any]:
        """Exchange a fresh signed workload assertion, optionally acting for a consenting user."""
        self._scopes(scopes)
        body: dict[str, Any] = {"grant_type": TOKEN_EXCHANGE_GRANT, "subject_token_type": JWT_TOKEN_TYPE, "subject_token": subject_token, "organization_id": organization_id, "subject_binding_id": subject_binding_id, "resource": resource, "scope": " ".join(scopes)}
        delegated = (actor_binding_id, actor_token, consent_id)
        if any(value is not None for value in delegated):
            if not all(delegated):
                raise ValueError("Delegation requires actor_binding_id, actor_token and consent_id together")
            body.update(actor_binding_id=actor_binding_id, actor_token=actor_token, actor_token_type=JWT_TOKEN_TYPE, consent_id=consent_id)
        return self._credential(body)

    def down_exchange(self, token: str, *, subject_resource: str, resource: str, scopes: list[str] | None = None) -> dict[str, Any]:
        """Bind the ingress resource exactly; omitted scopes retain the parent's scopes."""
        self._token(token)
        body: dict[str, Any] = {"grant_type": TOKEN_EXCHANGE_GRANT, "subject_token_type": ACCESS_TOKEN_TYPE, "subject_token": token, "subject_resource": subject_resource, "resource": resource}
        if scopes is not None:
            self._scopes(scopes)
            body["scope"] = " ".join(scopes)
        return self._credential(body)

    def introspect(self, token: str) -> dict[str, Any]:
        """Live validation for API-audience credentials; no positive authorization cache."""
        self._token(token)
        return self._post("/identity/introspect", {}, token)

    def credential_provider(self, fresh_assertion: Callable[[], dict[str, Any]]) -> Callable[[], str]:
        """Get a fresh assertion on every acquisition; pass the result to refresh_credential()."""
        def acquire() -> str:
            return str(self.exchange(**fresh_assertion())["access_token"])
        return acquire

    @staticmethod
    def _token(token: str) -> None:
        if not isinstance(token, str) or not re.fullmatch(r"pfa_[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}", token):
            raise ValueError("Expected a federated access token")

    @staticmethod
    def _scopes(scopes: list[str]) -> None:
        if not isinstance(scopes, list) or not 1 <= len(scopes) <= 64 or any(not isinstance(s, str) or s == "*" or not re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e]{1,128}", s) for s in scopes):
            raise ValueError("Explicit non-empty OAuth scopes are required")

    def _credential(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self._post("/oauth/token-exchange", body)
        self._token(result.get("access_token"))
        ttl = result.get("expires_in")
        if result.get("token_type") != "Bearer" or result.get("issued_token_type") != ACCESS_TOKEN_TYPE or isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 300 or not isinstance(result.get("scope"), str):
            raise IdentityError("Invalid credential response")
        return result

    def _post(self, path: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # One request only: replaying a signed assertion after an ambiguous failure is unsafe.
        with self._http.stream("POST", self._base + path, json=body, headers=headers) as response:
            if not 200 <= response.status_code < 300:
                raise IdentityError(f"Identity request rejected ({response.status_code})")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 65536:
                    raise IdentityError("Identity response exceeds limit")
                chunks.append(chunk)
            import json
            result = json.loads(b"".join(chunks))
            if not isinstance(result, dict):
                raise IdentityError("Invalid identity response")
            return result
