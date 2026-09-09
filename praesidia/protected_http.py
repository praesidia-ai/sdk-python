"""Durable exact-request HTTP execution and independent target receipt verification."""
from __future__ import annotations
import base64
import hashlib
import os
import re
from datetime import datetime
from typing import Any
from ._http import HttpClient, path_segment
from ._crypto import ed25519_verify
from ._jcs_canonical import jcs_canonicalize, jcs_commitment

_ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")

def _public_key(pem: str) -> bytes:
    body = pem.replace("-----BEGIN PUBLIC KEY-----", "").replace("-----END PUBLIC KEY-----", "")
    der = base64.b64decode("".join(body.split()), validate=True)
    if len(der) != 44 or not der.startswith(_ED25519_SPKI):
        raise ValueError("Target pin must be an Ed25519 SPKI public key")
    return der

def http_target_key_fingerprint(public_key_pem: str) -> str:
    return hashlib.sha256(_public_key(public_key_pem)).hexdigest()

def verify_http_receipt(receipt: Any, public_key_pem: str, expected: dict[str, str]) -> bool:
    """Pins come from the verifier's trust configuration, never from receipt contents."""
    try:
        if not isinstance(receipt, dict) or set(receipt) != {"statement", "signature"}: return False
        s = receipt["statement"]
        if not isinstance(s, dict) or set(s) != {"version", "actionId", "organizationId", "targetId", "keyId", "requestCommitment", "resultCommitment", "effect", "issuedAt", "targetTransactionId"}: return False
        if s["version"] != "praesidia.http-receipt.v1" or s["effect"] not in {"succeeded", "failed_no_effect", "partial", "unknown"}: return False
        if any(s.get(k) != v for k, v in expected.items()): return False
        if not isinstance(s["targetTransactionId"], str) or not s["targetTransactionId"].strip() or len(s["targetTransactionId"]) > 256: return False
        if not isinstance(s["issuedAt"], str) or not s["issuedAt"].endswith("Z"): return False
        instant = datetime.fromisoformat(s["issuedAt"][:-1] + "+00:00")
        if instant.isoformat(timespec="milliseconds").replace("+00:00", "Z") != s["issuedAt"]: return False
        for key in ("requestCommitment", "resultCommitment"):
            if not isinstance(s[key], str) or len(s[key]) != 64 or any(c not in "0123456789abcdef" for c in s[key]): return False
        signature = base64.b64decode(receipt["signature"], validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode() != receipt["signature"]: return False
        return ed25519_verify(jcs_canonicalize(s), signature, _public_key(public_key_pem)[12:])
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def verify_protected_http_result(result: dict[str, Any], original: dict[str, Any], target: dict[str, str], organization_id: str) -> bool:
    """Verify original request, observed result, identity pin and signed target assertion."""
    try:
        if original["targetId"] != target["targetId"]: return False
        request = {"version": "praesidia.http-request.v1", "targetId": target["targetId"],
            "destination": target["destination"], "targetKeyFingerprint": http_target_key_fingerprint(target["publicKeyPem"]),
            "method": "POST", "contentType": "application/json", "body": original["body"]}
        expected_closure = {"succeeded": "SUCCEEDED", "failed_no_effect": "FAILED_NO_EFFECT", "partial": "PARTIAL", "unknown": "OUTCOME_UNKNOWN"}.get(result["receipt"]["statement"]["effect"])
        if not expected_closure or result["closure"] != expected_closure: return False
        rc = jcs_commitment(request)
        result_c = jcs_commitment(result["result"])
        return result["requestCommitment"] == rc and result["resultCommitment"] == result_c and verify_http_receipt(result["receipt"], target["publicKeyPem"], {
            "actionId": result["actionId"], "organizationId": organization_id, "targetId": target["targetId"],
            "keyId": target["keyId"], "requestCommitment": rc, "resultCommitment": result_c})
    except (ValueError, TypeError, KeyError): return False

class ProtectedHttpResource:
    """Requires agents:invoke, workflows.execute and a user-backed credential.
    Resume is single use. Read the checkpoint after a lost response; never retry dispatch.
    """
    def __init__(self, http: HttpClient, runtime_installation_id: str | None = None) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/protected-actions/http"
        installation_id = runtime_installation_id if runtime_installation_id is not None else os.environ.get("PRAESIDIA_RUNTIME_INSTALLATION_ID")
        if installation_id is not None and (not isinstance(installation_id, str) or not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", installation_id)):
            raise ValueError("runtime_installation_id must be a UUID")
        self._runtime_installation_id = installation_id.lower() if installation_id else None
    @property
    def runtime_installation_id(self) -> str | None:
        return self._runtime_installation_id
    def bind_installation(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.runtime_installation_id is None: return request
        checkpoint = dict(request["checkpoint"])
        explicit = checkpoint.get("installationId")
        if explicit is not None and (not isinstance(explicit, str) or explicit.lower() != self.runtime_installation_id):
            raise ValueError("Checkpoint installation conflicts with the configured runtime installation")
        checkpoint["installationId"] = self.runtime_installation_id
        return {**request, "checkpoint": checkpoint}
    def prepare(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._http.post(f"{self._base}/prepare", json=self.bind_installation(request))
    def checkpoint(self, approval_id: str) -> dict[str, Any]:
        return self._http.get(f"{self._base}/checkpoints/{path_segment(approval_id, 'approval_id')}")
    def revoke(self, approval_id: str) -> dict[str, Any]:
        return self._http.post(f"{self._base}/checkpoints/{path_segment(approval_id, 'approval_id')}/revoke", json={})
    def resume(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._http.post(f"{self._base}/resume", json=self.bind_installation(request))
    def acknowledge(self, result: dict[str, Any]) -> dict[str, Any]:
        commitment = jcs_commitment(result["result"])
        if commitment != result["resultCommitment"]: raise ValueError("Observed result commitment mismatch")
        return self._http.post(f"{self._base}/acknowledge", json={"approvalId": result["approvalId"], "resultCommitment": commitment})
