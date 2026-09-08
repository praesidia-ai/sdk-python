"""SCAN2-013/CT-10 -- TS SDK sweep companion check for sdk-python.

CT-10 found the API key is a runtime-enumerable, JSON/`util.inspect`-visible
field on the TS SDK's `PraesidiaClient`/`PraesidiaGuard` (TypeScript's
`private` is compile-time only). The equivalent Python risk surface is
`__repr__`/`__str__`/`__dict__`/`vars()` -- but Python's DEFAULT `repr()`/
`str()` for a class with no explicit override does NOT dump `__dict__` (it
prints `<module.Class object at 0x...>`), so accidental `print(client)` /
default logging does NOT leak the key the way `console.log`/`JSON.stringify`
does in Node. `praesidia/_http.py`'s `HttpClient` and `praesidia/client.py`'s
`Praesidia` both have no `__repr__`/`__str__` override (grep-verified: the
only override in the whole package is `PraesidiaError.__repr__`, which does
not hold the key -- see `test_error_envelope.py`'s
`test_api_key_never_reaches_message_repr_or_str`). This test locks that
guarantee in so a future `__repr__` added to either class can't silently
reintroduce the leak. `vars()`/`__dict__` DO expose the key (`HttpClient`
stores `Authorization: Bearer <key>` in `self._headers`) -- that requires a
caller to deliberately introspect internals, not default
logging/serialization, so it is out of this ticket's "accidental whole-object
serialization" scope (documented, not fixed -- matching CT-10's own
asymmetry finding).
"""

from __future__ import annotations

from praesidia import Praesidia

SENTINEL_KEY = "sk_live_SENTINEL_DO_NOT_LEAK_0000000000"


def test_default_str_and_repr_do_not_leak_the_api_key():
    client = Praesidia(api_key=SENTINEL_KEY, org_id="org-1", base_url="http://test.local")
    assert SENTINEL_KEY not in str(client)
    assert SENTINEL_KEY not in repr(client)
    # The transport object is the one that actually stores the credential.
    http = client._http
    assert SENTINEL_KEY not in str(http)
    assert SENTINEL_KEY not in repr(http)
