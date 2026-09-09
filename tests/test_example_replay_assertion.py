import importlib.util
from pathlib import Path

import httpx
import pytest

from praesidia.exceptions import PraesidiaError

spec = importlib.util.spec_from_file_location("replay_assertion", Path(__file__).parents[1] / "examples/protected_http_replay_assertion.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PATH = "/organizations/11111111-1111-4111-8111-111111111111/protected-actions/http/resume"
MESSAGE = "Approval is not consumable (not approved, already consumed, or wrong tenant)"


def rejected(status=400, path=PATH, message=MESSAGE):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.test" + path),
                              json={"statusCode": status, "path": path, "message": message})
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as cause:
        raise PraesidiaError(str(cause), status_code=status) from cause


def test_exact_backend_replay_rejection():
    module.assert_replay_rejected(rejected, PATH)


@pytest.mark.parametrize("kwargs", [{"status": 401}, {"status": 500}, {"path": "/other"}, {"message": "Unrelated invalid input"}])
def test_other_api_errors_do_not_prove_replay_rejection(kwargs):
    with pytest.raises((PraesidiaError, RuntimeError)):
        module.assert_replay_rejected(lambda: rejected(**kwargs), PATH)


def test_network_failure_does_not_prove_replay_rejection():
    def timeout():
        raise httpx.ReadTimeout("timed out")
    with pytest.raises(httpx.ReadTimeout):
        module.assert_replay_rejected(timeout, PATH)


def test_success_does_not_prove_replay_rejection():
    with pytest.raises(RuntimeError, match="unexpectedly accepted"):
        module.assert_replay_rejected(lambda: {}, PATH)
