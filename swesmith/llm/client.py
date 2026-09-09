"""GLM-5.3-Flash client for the internal PTIE endpoint."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from dataclasses import dataclass
from typing import Any


DEFAULT_MODEL = "glm-5.3-flash"
DEFAULT_API_BASE = "http://127.0.0.1:8000/v1"
DEFAULT_TIMEOUT = 300.0


class PTIEError(RuntimeError):
    """Raised when the internal PTIE endpoint cannot serve a request."""


@dataclass(frozen=True)
class PTIEMessage:
    role: str
    content: str


@dataclass(frozen=True)
class PTIEChoice:
    index: int
    message: PTIEMessage
    finish_reason: str | None = None


@dataclass(frozen=True)
class PTIEResponse:
    id: str
    model: str
    choices: list[PTIEChoice]
    usage: dict[str, int] | None = None


def completion_kwargs(
    *, model: str | None = None, api_base: str | None = None
) -> dict[str, Any]:
    """Return defaults for a request to the local PTIE inference service."""
    return {
        "model": model or os.getenv("SWESMITH_MODEL", DEFAULT_MODEL),
        "api_base": api_base or os.getenv("SWESMITH_API_BASE", DEFAULT_API_BASE),
        "reasoning_effort": os.getenv("GLM_REASONING_EFFORT", "high"),
    }


def estimate_tokens(text: str) -> int:
    """Estimate tokens without consulting a remote tokenizer service."""
    return max(1, (len(text) + 3) // 4)


def _completion_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _response_from_payload(payload: dict[str, Any]) -> PTIEResponse:
    try:
        choices = [
            PTIEChoice(
                index=int(choice.get("index", index)),
                message=PTIEMessage(
                    role=choice.get("message", {}).get("role", "assistant"),
                    content=choice.get("message", {}).get("content") or "",
                ),
                finish_reason=choice.get("finish_reason"),
            )
            for index, choice in enumerate(payload["choices"])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise PTIEError("PTIE returned an invalid chat completion response") from exc
    usage = payload.get("usage")
    return PTIEResponse(
        id=str(payload.get("id", "")),
        model=str(payload.get("model", DEFAULT_MODEL)),
        choices=choices,
        usage=usage if isinstance(usage, dict) else None,
    )


def complete(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    api_base: str | None = None,
    **kwargs: Any,
) -> PTIEResponse:
    """Call PTIE's chat completions route without credentials."""
    settings = completion_kwargs(model=model, api_base=api_base)
    settings.update(kwargs)
    request_settings = {
        key: value
        for key, value in settings.items()
        if key not in {"api_base", "timeout", "headers"} and value is not None
    }
    request = urllib.request.Request(
        _completion_url(str(settings["api_base"])),
        data=json.dumps({"messages": messages, **request_settings}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = float(
        settings.get("timeout", os.getenv("SWESMITH_API_TIMEOUT", DEFAULT_TIMEOUT))
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PTIEError(f"PTIE request failed with HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PTIEError(f"PTIE request failed: {exc}") from exc
    return _response_from_payload(payload)

