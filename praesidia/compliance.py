"""
Praesidia SDK — ComplianceResource.

Programmatic export of the EU AI Act auditor/DPO compliance report (Q1-04).

Endpoint base: ``/organizations/{org_id}/compliance/eu-ai-act/reports``

Report generation is asynchronous: request a report, poll its status until it
is ``ready``, then download the structured JSON and/or the rendered PDF.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ._http import HttpClient, path_segment
from .exceptions import PraesidiaError

_DEFAULT_POLL_INTERVAL = 2.0  # seconds
_DEFAULT_TIMEOUT = 120.0  # seconds


class ComplianceResource:
    """
    Generate and download EU AI Act auditor compliance reports.

    Endpoint base: ``/organizations/{org_id}/compliance/eu-ai-act/reports``

    Requires the ``COMPLIANCE_MANAGE`` permission to create a report and
    ``COMPLIANCE_VIEW`` to poll status / download artifacts.

    Example::

        report = client.compliance.generate_and_wait()
        doc = client.compliance.get_json(report["reportId"])
        pdf = client.compliance.get_pdf(report["reportId"])
        with open("eu-ai-act-report.pdf", "wb") as fh:
            fh.write(pdf)
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = (
            f"/organizations/{http.org_id}/compliance/eu-ai-act/reports"
        )

    def request_report(self) -> dict[str, Any]:
        """
        Enqueue a new auditor report.

        Calls ``POST /organizations/{org_id}/compliance/eu-ai-act/reports``.

        Returns:
            A dict with ``reportId``, ``jobId`` (str or None) and ``status``
            (``"pending" | "processing" | "completed" | "failed"``).
        """
        return self._http.post(self._base)

    def get_status(self, report_id: str) -> dict[str, Any]:
        """
        Poll the generation status of a report.

        Calls ``GET .../reports/{report_id}``.

        Args:
            report_id: The id returned by :meth:`request_report`.

        Returns:
            A status dict — ``reportId``, ``status``, ``ready`` (bool),
            ``pdfByteLength`` (int or None), ``error`` (str or None),
            ``requestedAt`` and ``completedAt`` (str or None).
        """
        return self._http.get(
            f"{self._base}/{path_segment(report_id, 'report_id')}"
        )

    def get_json(self, report_id: str) -> dict[str, Any]:
        """
        Download the structured JSON auditor report (schemaVersion ``q1-04-v1``).

        Calls ``GET .../reports/{report_id}/json``.

        Raises:
            PraesidiaError: HTTP 409 if the report is not yet ``completed``.

        Returns:
            The full report document dict.
        """
        return self._http.get(
            f"{self._base}/{path_segment(report_id, 'report_id')}/json"
        )

    def get_pdf(self, report_id: str) -> bytes:
        """
        Download the rendered PDF auditor report as raw bytes.

        Calls ``GET .../reports/{report_id}/pdf``.

        Raises:
            PraesidiaError: HTTP 409 if the report is not yet ``completed``.

        Returns:
            Raw PDF bytes — write with ``open(path, "wb").write(pdf)``.
        """
        r = self._http.stream_get(
            f"{self._base}/{path_segment(report_id, 'report_id')}/pdf"
        )
        self._http._raise_for_status(r)
        return r.content

    def wait_for_report(
        self,
        report_id: str,
        timeout: float = _DEFAULT_TIMEOUT,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> dict[str, Any]:
        """
        Poll ``get_status`` until the report is ready (or fails / times out).

        Args:
            report_id:     The id returned by :meth:`request_report`.
            timeout:       Give up after this many seconds (default: 120).
            poll_interval: Delay between status polls in seconds (default: 2).

        Returns:
            The final ``ready`` status dict.

        Raises:
            PraesidiaError: If the report status becomes ``failed``.
            TimeoutError:   If ``timeout`` elapses before the report is ready.
        """
        timeout = _validate_wait_value(timeout, "timeout", allow_zero=False)
        poll_interval = _validate_wait_value(
            poll_interval, "poll_interval", allow_zero=True
        )
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_status(report_id)
            if status.get("status") == "failed":
                raise PraesidiaError(
                    f"Praesidia report {report_id} failed: "
                    f"{status.get('error') or 'unknown error'}"
                )
            if status.get("ready"):
                return status
            if time.monotonic() + poll_interval > deadline:
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for Praesidia report "
                    f"{report_id} (last status: {status.get('status')})"
                )
            time.sleep(poll_interval)

    def generate_and_wait(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> dict[str, Any]:
        """
        Convenience helper: request a new report and poll until it is ready.

        Args:
            timeout:       Give up after this many seconds (default: 120).
            poll_interval: Delay between status polls in seconds (default: 2).

        Returns:
            The final ``ready`` status dict (carries ``reportId``); pass
            ``status["reportId"]`` to :meth:`get_json` / :meth:`get_pdf`.
        """
        created = self.request_report()
        return self.wait_for_report(
            created["reportId"],
            timeout=timeout,
            poll_interval=poll_interval,
        )


def _validate_wait_value(value: float, name: str, *, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number of seconds")
    result = float(value)
    minimum_valid = result >= 0 if allow_zero else result > 0
    if not minimum_valid or not math.isfinite(result):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{name} must be finite and {comparison}")
    return result
