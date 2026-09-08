"""
SCAN2-011 — shared pagination helpers for the list families
(``AgentsResource.list``, ``ConnectionsResource.list``, ``WorkflowsResource.list``
/ ``.list_runs``) that previously unwrapped be's paginated envelope into a bare
list typed as "the collection", silently discarding ``meta``
(``page``/``limit``/``total``/``totalPages``/``hasNextPage``) and any
truncation signal along with it (``be/src/common/dto/pagination.dto.ts``'s
``createPaginatedResult``).

Backwards compatibility decision (mirrors the TS SDK's SCAN2-011 fix so both
SDKs make the same call): each family's existing ``list()`` (or
``list_runs()``) keeps its exact prior signature -- ``list[dict[str, Any]]``,
still just the first page, still no truncation signal -- because changing a
published method's return type is a breaking change. The fix is additive:
``list_page()`` exposes the full envelope, and ``list_all()`` is an
auto-paginating generator so "give me all of them" is correct by default
(mirrors ``AuditResource.stream``'s established BUGHUNT-SDK-01 precedent,
``praesidia/audit.py``) instead of correct only if the caller remembers to
page. ``list()``/``list_runs()`` are documented (docstring + README) as
returning only the first page.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator


def normalize_paged_envelope(
    result: list[dict[str, Any]] | dict[str, Any],
    data_key: str,
) -> dict[str, Any]:
    """
    Normalize a list response into ``{"data": [...], "total": int, "meta": {...}}``
    regardless of shape: be's real ``{data, total, meta}`` envelope, a
    legacy/alternate ``{<data_key>: [...]}`` shape, or (defensively) a bare
    list. A bare list or a response with no ``meta`` is treated as a single
    complete page -- there is no pagination signal to preserve, so
    ``hasNextPage: False`` is the honest default, not an assumption that more
    data doesn't exist.
    """
    if isinstance(result, list):
        return {
            "data": result,
            "total": len(result),
            "meta": {
                "page": 1,
                "limit": len(result),
                "total": len(result),
                "totalPages": 1,
                "hasNextPage": False,
            },
        }
    data = result.get("data", result.get(data_key, []))
    meta = result.get("meta")
    total = result.get("total", meta.get("total") if meta else len(data))
    return {
        "data": data,
        "total": total,
        "meta": meta
        or {
            "page": 1,
            "limit": len(data),
            "total": total,
            "totalPages": 1,
            "hasNextPage": False,
        },
    }


def paginate_all(
    fetch_page: Callable[[int], dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """
    Auto-paginate across every page returned by ``fetch_page``, yielding items
    lazily.

    Terminal condition is an EMPTY page, not ``meta["hasNextPage"]``/a short
    page -- matching ``AuditResource.stream``'s established BUGHUNT-SDK-01
    precedent (``praesidia/audit.py``): trusting ``hasNextPage`` alone
    reintroduces the same class of off-by-one this ticket exists to close if
    a future backend response ever mis-sets it, whereas an empty page is
    unambiguous.
    """
    page = 1
    while True:
        envelope = fetch_page(page)
        data = envelope["data"]
        if not data:
            return
        yield from data
        page += 1
