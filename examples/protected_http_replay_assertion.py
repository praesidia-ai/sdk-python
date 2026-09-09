"""Example-only assertion: backend replay refusal is distinct from transport failure."""
from __future__ import annotations

import httpx
from praesidia.exceptions import PraesidiaError


def assert_replay_rejected(resume, path: str) -> None:
    try:
        resume()
    except PraesidiaError as error:
        cause = error.__cause__
        if error.status_code != 400 or not isinstance(cause, httpx.HTTPStatusError):
            raise
        response = cause.response
        body = response.json()
        if (response.status_code != 400 or response.request.url.path != path
                or body.get("statusCode") != 400 or body.get("path") != path
                or body.get("message") != "Approval is not consumable (not approved, already consumed, or wrong tenant)"):
            raise RuntimeError("Replay check received an unrelated API error") from error
        return
    raise RuntimeError("Resume replay unexpectedly accepted")
