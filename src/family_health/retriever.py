from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .source_registry import SourceUrlRejected, validate_url_for_source


class RetrievalError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoAutomaticRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SafeHttpTransport:
    """Bounded read-only transport for public medical-source records.

    URLs are generated or source-contract validated before this class sees
    them. The transport still rechecks every request and redirect, rejects any
    non-public DNS answer, sends no body/auth/cookie, and never persists data.
    """

    JSON_TYPES = frozenset({"application/json", "application/fhir+json"})
    TEXT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})

    def __init__(
        self,
        *,
        opener=None,
        resolver: Callable[..., list] = socket.getaddrinfo,
        timeout_seconds: float = 12.0,
        max_bytes: int = 512_000,
        max_redirects: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.opener = opener or build_opener(_NoAutomaticRedirect())
        self.resolver = resolver
        self.timeout_seconds = min(max(float(timeout_seconds), 1.0), 15.0)
        self.max_bytes = min(max(int(max_bytes), 64), 1_000_000)
        self.max_redirects = min(max(int(max_redirects), 0), 3)
        self.sleep = sleep
        self.clock = clock
        self._rate_lock = threading.Lock()
        self._last_request_by_host: dict[str, float] = {}

    def _validate_target(self, source_id: str, url: str) -> str:
        try:
            validate_url_for_source(source_id, url, allow_query=True)
        except SourceUrlRejected as exc:
            raise RetrievalError(f"source_url_rejected:{exc.code}") from exc
        parsed = urlparse(url)
        host = parsed.hostname or ""
        try:
            answers = self.resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RetrievalError("dns_resolution_failed") from exc
        addresses = {str(answer[4][0]) for answer in answers if len(answer) >= 5 and answer[4]}
        if not addresses:
            raise RetrievalError("dns_resolution_empty")
        try:
            if any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise RetrievalError("non_public_dns_answer")
        except ValueError as exc:
            raise RetrievalError("invalid_dns_answer") from exc
        return host

    def _respect_rate_limit(self, host: str) -> None:
        minimum_interval = 0.35 if host.endswith("ncbi.nlm.nih.gov") else 0.05
        with self._rate_lock:
            now = self.clock()
            previous = self._last_request_by_host.get(host)
            if previous is not None:
                wait = minimum_interval - (now - previous)
                if wait > 0:
                    self.sleep(wait)
            self._last_request_by_host[host] = self.clock()

    def _request_once(self, url: str, *, accept: str):
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": accept,
                "User-Agent": "DoctorcandidateEvidence/1.0 (local read-only source gateway)",
                "Cache-Control": "no-store",
            },
        )
        try:
            return self.opener.open(request, timeout=self.timeout_seconds)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                return exc
            raise RetrievalError(f"http_status:{exc.code}") from exc
        except URLError as exc:
            raise RetrievalError("network_unavailable") from exc
        except TimeoutError as exc:
            raise RetrievalError("network_timeout") from exc

    def _read(self, source_id: str, url: str, *, accept: str) -> tuple[bytes, str, str]:
        current = url
        for redirect_count in range(self.max_redirects + 1):
            host = self._validate_target(source_id, current)
            self._respect_rate_limit(host)
            response = self._request_once(current, accept=accept)
            try:
                status = int(getattr(response, "status", getattr(response, "code", 200)))
                if 300 <= status < 400:
                    location = response.headers.get("Location")
                    if not location or redirect_count >= self.max_redirects:
                        raise RetrievalError("redirect_limit_or_missing_location")
                    target = urljoin(current, location)
                    try:
                        validate_url_for_source(source_id, target, allow_query=True)
                    except SourceUrlRejected as exc:
                        raise RetrievalError(f"redirect_rejected:{exc.code}") from exc
                    current = target
                    continue
                if status != 200:
                    raise RetrievalError(f"http_status:{status}")
                content_type = response.headers.get_content_type()
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise RetrievalError("response_too_large")
                return body, content_type, current
            finally:
                response.close()
        raise RetrievalError("redirect_limit")

    def get_json(self, source_id: str, url: str) -> dict[str, Any]:
        body, content_type, _final_url = self._read(
            source_id, url, accept="application/json"
        )
        if content_type not in self.JSON_TYPES and not content_type.endswith("+json"):
            raise RetrievalError("json_content_type_required")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RetrievalError("invalid_json_response") from exc
        if not isinstance(payload, dict):
            raise RetrievalError("json_object_required")
        return payload

    def get_xml(self, source_id: str, url: str) -> bytes:
        # DailyMed returns 406 for an explicit application/xml Accept value.
        # The URL and response type are still source-contract checked.
        body, content_type, _final_url = self._read(source_id, url, accept="*/*")
        if content_type not in {"application/xml", "text/xml"} and not content_type.endswith("+xml"):
            raise RetrievalError("xml_content_type_required")
        if not body.lstrip().startswith(b"<"):
            raise RetrievalError("invalid_xml_response")
        return body

    def get_text(self, source_id: str, url: str) -> tuple[str, str]:
        body, content_type, final_url = self._read(
            source_id,
            url,
            accept="text/html,application/xhtml+xml,text/plain;q=0.8",
        )
        if content_type not in self.TEXT_TYPES:
            raise RetrievalError("html_or_text_content_type_required")
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RetrievalError("invalid_text_response") from exc
        if "<" not in text and not text.strip():
            raise RetrievalError("empty_text_response")
        return text, final_url
