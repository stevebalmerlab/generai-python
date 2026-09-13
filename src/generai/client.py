"""Synchronous, thread-safe HTTP client for /api/v1 (Python 3.10+)."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .errors import APIError, JobFailed, PollingTimeout, ProtocolError, TransportError

JSON = dict[str, Any]
CATEGORY_TYPES = frozenset({"txt2img", "edit", "safe", "nsfw"})
MODELS = frozenset({"txt2img", "edit", "wan22", "minimax_h3"})
FILE_FIELDS = frozenset({"input_image", "input_image_2", "input_image_3", "input_video"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MIME_EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm",
}


def new_idempotency_key() -> str:
    """Create a key for ONE intended generation. Persist before sending."""
    return str(uuid4())


def credits(value: str | int | float | Decimal) -> Decimal:
    """Convert API cost/balance/refunded values without binary float arithmetic."""
    return Decimal(str(value))


def _positive_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**53 - 1:
        raise ValueError("ID/page must be a positive safe integer")
    return value


def _category_type(value: str) -> str:
    if value not in CATEGORY_TYPES:
        raise ValueError(f"category_type must be one of {sorted(CATEGORY_TYPES)}")
    return value


def _fields(category_type: str, category_id: int, options: Mapping[str, Any]) -> JSON:
    result: JSON = {
        "category_type": _category_type(category_type), "category_id": _positive_id(category_id)
    }
    for name, value in options.items():
        if name in FILE_FIELDS or name in {"category_type", "category_id"}:
            raise ValueError(f"{name} must be passed as its dedicated argument")
        if value is None:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif name == "dialogue_edits" and isinstance(value, Mapping):
            value = json.dumps(dict(value), ensure_ascii=False)
        elif isinstance(value, Decimal):
            value = str(value)
        elif not isinstance(value, (str, int, float)):
            raise ValueError(f"Unsupported option value: {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Non-finite option: {name}")
        result[name] = value
    return result


class GenerAI:
    """All public generation operations. Use as a context manager to close connections.

    Responses are dictionaries matching the API, including new server fields.
    Model capability validation belongs to the current server catalog; the SDK
    does not cache categories or prices and does not invent unsupported endpoints.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("GENERAI_API_KEY", "")
        if not key or key != key.strip() or any(char.isspace() for char in key):
            raise ValueError("Provide api_key or GENERAI_API_KEY (without whitespace)")
        base_url = base_url or os.environ.get("GENERAI_BASE_URL", "https://generai.org")
        url = urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("base_url must be an origin, e.g. https://generai.org, without /api/v1")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        if not math.isfinite(retry_delay) or retry_delay < 0:
            raise ValueError("retry_delay must be nonnegative and finite")
        self._key = key
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, follow_redirects=False,
            headers={"Authorization": f"Bearer {key}", "User-Agent": "generai-python/0.1.0"},
            transport=transport,
        )

    def __enter__(self) -> GenerAI:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _redact(self, value: Any) -> str:
        return str(value).replace(self._key, "[REDACTED]")[:1000]

    def _error(self, response: httpx.Response, key: str | None) -> APIError:
        code, message = "http_error", "Request failed"
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = self._redact(error.get("code", code))
                message = self._redact(error.get("message", message))
        except ValueError:
            message = "Non-JSON error response (proxy, gateway or server)"
        return APIError(response.status_code, code, message, idempotency_key=key)

    def _delay(self, response: httpx.Response | None, attempt: int) -> float:
        delay = min(30.0, self._retry_delay * 2**min(attempt, 10))
        if response is not None:
            value = response.headers.get("Retry-After")
            if value:
                try:
                    seconds = float(value)
                except ValueError:
                    try:
                        date = parsedate_to_datetime(value)
                        if date.tzinfo is None:
                            date = date.replace(tzinfo=timezone.utc)
                        seconds = (date - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        seconds = delay
                if math.isfinite(seconds):
                    delay = min(60.0, max(0.0, seconds))
        return delay

    def _request(
        self, method: str, path: str, *, fields: JSON | None = None,
        uploads: Mapping[str, Path] | None = None, key: str | None = None,
        params: JSON | None = None, stream: bool = False, deadline: float | None = None,
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Polling deadline reached")
            response = None
            try:
                with ExitStack() as stack:
                    kwargs: JSON = {}
                    if key is not None:
                        # httpx 'data' alone is urlencoded. Text-only generations
                        # MUST also be multipart, hence (None, value) file parts.
                        parts = [(name, (None, str(value))) for name, value in (fields or {}).items()]
                        for name, file in (uploads or {}).items():
                            handle = stack.enter_context(file.open("rb"))
                            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                            parts.append((name, (file.name, handle, mime)))
                        kwargs["files"] = parts
                    elif fields is not None:
                        kwargs["json"] = fields
                    request = self._http.build_request(
                        method, "/api/v1/" + path, params=params,
                        headers={"Idempotency-Key": key} if key else None,
                        timeout=min(self._timeout, remaining) if remaining is not None else self._timeout,
                        **kwargs,
                    )
                    response = self._http.send(request, stream=stream)
                    if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                        delay = self._delay(response, attempt)
                        response.close()
                    elif not response.is_success:
                        try:
                            response.read()
                            error = self._error(response, key)
                        finally:
                            response.close()
                        raise error
                    else:
                        return response
            except httpx.TransportError:
                if response is not None:
                    response.close()
                if attempt >= self._max_retries:
                    raise TransportError(idempotency_key=key) from None
                delay = self._delay(None, attempt)
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.monotonic()))
            time.sleep(delay)
        raise AssertionError("unreachable")

    def _json(self, method: str, path: str, **kwargs: Any) -> JSON:
        response = self._request(method, path, **kwargs)
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Expected object")
            return body
        except ValueError:
            raise ProtocolError(
                "Expected a JSON object from the API", idempotency_key=kwargs.get("key")
            ) from None
        finally:
            response.close()

    def me(self) -> JSON:
        return self._json("GET", "me")

    def openapi(self) -> JSON:
        return self._json("GET", "openapi")

    def categories(self, *, model: str | None = None, category_type: str | None = None) -> list[JSON]:
        params = {}
        if model is not None:
            if model not in MODELS:
                raise ValueError(f"model must be one of {sorted(MODELS)}")
            params["model"] = model
        if category_type is not None:
            params["category_type"] = _category_type(category_type)
        return self._json("GET", "categories", params=params)["items"]

    def category(self, category_type: str, category_id: int) -> JSON:
        return self._json("GET", f"categories/{_category_type(category_type)}/{_positive_id(category_id)}")

    def quote(self, category_type: str, category_id: int, **options: Any) -> JSON:
        """No charge, no upload validation, no price reservation."""
        return self._json("POST", "generations/quote", fields=_fields(category_type, category_id, options))

    def create(
        self, category_type: str, category_id: int, *,
        files: Mapping[str, str | os.PathLike[str]] | None = None,
        idempotency_key: str | None = None, **options: Any,
    ) -> JSON:
        """Create one paid job. Retrying the same key AND payload does not charge again.

        Local file paths only; each retry reopens them from byte zero. Do not modify
        files during submission/retries. Success includes SDK-only idempotency_key.
        """
        key = new_idempotency_key() if idempotency_key is None else idempotency_key
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
            raise ValueError("Invalid idempotency_key")
        fields = _fields(category_type, category_id, options)
        uploads = {}
        for name, file in (files or {}).items():
            if name not in FILE_FIELDS:
                raise ValueError(f"Unknown upload field: {name}")
            path = Path(file)
            if not path.is_file():
                raise FileNotFoundError(path)
            limit = 16_000_000 if name == "input_video" else 10_000_000
            if not 0 < path.stat().st_size <= limit:
                raise ValueError(f"{name} must contain 1..{limit} bytes")
            uploads[name] = path
        body = self._json("POST", "generations", fields=fields, uploads=uploads, key=key)
        body["idempotency_key"] = key
        return body

    def create_image(self, category_id: int, prompt: str, **options: Any) -> JSON:
        return self.create("txt2img", category_id, user_prompt=prompt, **options)

    def edit_image(
        self, category_id: int, image: str | os.PathLike[str], *,
        second_image: str | os.PathLike[str] | None = None,
        prompt: str | None = None, **options: Any,
    ) -> JSON:
        files = {"input_image": image}
        if second_image is not None:
            files["input_image_2"] = second_image
        return self.create("edit", category_id, files=files, user_prompt=prompt, **options)

    def create_video(self, category_type: str, category_id: int, **options: Any) -> JSON:
        if category_type not in {"safe", "nsfw"}:
            raise ValueError("Video category_type must be safe or nsfw")
        return self.create(category_type, category_id, **options)

    def generations(self, page: int = 1) -> JSON:
        return self._json("GET", "generations", params={"page": _positive_id(page)})

    def iter_generations(self, *, start_page: int = 1, max_pages: int | None = None) -> Iterator[JSON]:
        """Iterate history (newest first). Concurrent inserts can shift page boundaries."""
        page = _positive_id(start_page)
        if max_pages is not None:
            _positive_id(max_pages)
        count = 0
        while max_pages is None or count < max_pages:
            body = self.generations(page)
            items = body["items"]
            yield from items
            if not items or len(items) < body.get("page_size", 50):
                return
            page += 1
            count += 1

    def generation(self, job_id: int) -> JSON:
        return self._json("GET", f"generations/{_positive_id(job_id)}")

    def wait(
        self, job_id: int, *, timeout: float = 1800, poll_interval: float = 5,
        on_update: Callable[[JSON], None] | None = None, raise_on_failure: bool = True,
    ) -> JSON:
        """Poll terminal status, NOT progress=100. A timeout does not cancel the job.

        HTTP timeouts are capped to remaining polling time. They are per-network-
        operation, not a hard deadline for slow streaming or user callbacks.
        """
        _positive_id(job_id)
        if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive and finite")
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = self._json("GET", f"generations/{job_id}", deadline=deadline)
            except TimeoutError:
                break
            if on_update is not None:
                on_update(last)
            if last.get("status") in {"done", "failed", "cancelled"}:
                if raise_on_failure and last["status"] != "done":
                    raise JobFailed(last)
                return last
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        raise PollingTimeout(job_id, last)

    def download(
        self, job_id: int, destination: str | os.PathLike[str] = "downloads", *,
        overwrite: bool = False,
    ) -> Path:
        """Stream to an explicit filename, or existing directory. Publish only on success.

        Missing suffix-less destinations are created as directories. Never follows
        result_url/redirects or trusts server filenames. No partial final files.
        """
        _positive_id(job_id)
        target = Path(destination)
        is_directory = target.is_dir() or (not target.exists() and not target.suffix)
        parent = target if is_directory else target.parent
        parent.mkdir(parents=True, exist_ok=True)
        response = self._request("GET", f"generations/{job_id}/file", stream=True)
        temp_name = None
        try:
            if is_directory:
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                target = target / f"generation-{job_id}{MIME_EXTENSIONS.get(mime, '.bin')}"
            if target.exists() and not overwrite:
                raise FileExistsError(target)
            fd, temp_name = tempfile.mkstemp(prefix=".generai-", suffix=".part", dir=parent)
            written = 0
            with os.fdopen(fd, "wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)
                    written += len(chunk)
            expected = response.headers.get("content-length")
            if expected and not response.headers.get("content-encoding") and written != int(expected):
                raise ProtocolError("Incomplete download: Content-Length mismatch")
            if overwrite:
                os.replace(temp_name, target)
            else:
                # Atomic no-clobber publication, including concurrent downloads.
                os.link(temp_name, target)
            return target
        except httpx.TransportError:
            raise TransportError() from None
        finally:
            response.close()
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)
