"""Thin async wrapper over the official `google-genai` SDK.

- Model names come from config (role "fast" or "strong"), never hardcoded.
- generate_json() uses Gemini structured output with a Pydantic response schema.
- Retries with exponential backoff + jitter on 429/5xx/timeouts/malformed output.
- Every call is logged with tokens, latency and estimated cost, and sent to an optional sink
  (the DB `llm_calls` table in production).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import GeminiConfig

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Role = Literal["fast", "strong"]
RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class LLMOutputError(LLMError):
    """Model returned empty / non-conforming output."""


@dataclass(slots=True)
class LLMUsage:
    model: str
    purpose: str
    prompt_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    ok: bool
    error: str | None = None


UsageSink = Callable[[LLMUsage], Awaitable[None]]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, errors.APIError):
        return getattr(exc, "code", None) in RETRYABLE_HTTP
    return isinstance(exc, (LLMOutputError, httpx.TransportError, asyncio.TimeoutError))


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        cfg: GeminiConfig,
        *,
        usage_sink: UsageSink | None = None,
        client: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self._sink = usage_sink
        self.retry_wait = wait_exponential_jitter(initial=2, max=60)
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(cfg.timeout_s * 1000)),
        )

    # ── public API ─────────────────────────────────────────────────────────
    def model_for(self, role: Role) -> str:
        return self.cfg.fast_model if role == "fast" else self.cfg.strong_model

    async def upload_file(self, path: str, mime_type: str, *, wait_s: float = 90) -> Any:
        """Upload a document via the Files API (needed for PDFs over ~20 MB); waits until ACTIVE."""
        file = await self._client.aio.files.upload(file=path, config={"mime_type": mime_type})
        deadline = time.monotonic() + wait_s
        while getattr(getattr(file, "state", None), "name", "ACTIVE") == "PROCESSING":
            if time.monotonic() > deadline:
                raise LLMError(f"file {file.name} still processing after {wait_s}s")
            await asyncio.sleep(2)
            file = await self._client.aio.files.get(name=file.name)
        if getattr(getattr(file, "state", None), "name", "ACTIVE") == "FAILED":
            raise LLMError(f"file {file.name} processing failed")
        return file

    async def delete_file(self, name: str) -> None:
        try:
            await self._client.aio.files.delete(name=name)
        except Exception:
            log.warning("gemini.file_delete_failed", extra={"file": name})

    async def generate_json(
        self,
        prompt: str | list[Any],
        schema: type[T],
        *,
        role: Role = "fast",
        system: str | None = None,
        purpose: str = "",
        temperature: float | None = None,
    ) -> T:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.cfg.temperature if temperature is None else temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )

        def parse(resp: Any) -> T:
            parsed = getattr(resp, "parsed", None)
            if isinstance(parsed, schema):
                return parsed
            text = getattr(resp, "text", None)
            if not text:
                raise LLMOutputError("empty response")
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                raise LLMOutputError(f"schema mismatch: {e.errors()[:3]}") from e

        return await self._call(self.model_for(role), prompt, config, parse, purpose)

    async def generate_text(
        self,
        prompt: str | list[Any],
        *,
        role: Role = "strong",
        system: str | None = None,
        purpose: str = "",
        temperature: float | None = None,
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.cfg.temperature if temperature is None else temperature,
        )

        def parse(resp: Any) -> str:
            text = (getattr(resp, "text", None) or "").strip()
            if not text:
                raise LLMOutputError("empty response")
            return text

        return await self._call(self.model_for(role), prompt, config, parse, purpose)

    # ── internals ──────────────────────────────────────────────────────────
    def cost(self, model: str, prompt_tokens: int, output_tokens: int) -> float:
        price = self.cfg.prices.get(model)
        if not price:
            return 0.0
        return round(
            prompt_tokens * price.input_per_1m / 1e6 + output_tokens * price.output_per_1m / 1e6, 6
        )

    async def _call(self, model, prompt, config, parse, purpose):
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            wait=self.retry_wait,
            stop=stop_after_attempt(self.cfg.max_retries),
            reraise=True,
        ):
            with attempt:
                return await self._once(model, prompt, config, parse, purpose)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _once(self, model, prompt, config, parse, purpose):
        started = time.perf_counter()
        resp = None
        try:
            resp = await self._client.aio.models.generate_content(
                model=model, contents=prompt, config=config
            )
            result = parse(resp)
        except Exception as e:
            await self._record(model, purpose, resp, started, error=f"{type(e).__name__}: {e}")
            raise
        await self._record(model, purpose, resp, started)
        return result

    async def _record(self, model, purpose, resp, started, error: str | None = None) -> None:
        meta = getattr(resp, "usage_metadata", None)
        prompt_tokens = getattr(meta, "prompt_token_count", None) or 0
        # Thinking tokens are billed as output tokens.
        output_tokens = (getattr(meta, "candidates_token_count", None) or 0) + (
            getattr(meta, "thoughts_token_count", None) or 0
        )
        usage = LLMUsage(
            model=model,
            purpose=purpose,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            cost_usd=self.cost(model, prompt_tokens, output_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            ok=error is None,
            error=error[:2000] if error else None,
        )
        level = logging.INFO if usage.ok else logging.WARNING
        log.log(level, "gemini.call", extra=_usage_dict(usage))
        if self._sink:
            try:
                await self._sink(usage)
            except Exception:
                log.exception("gemini.usage_sink_failed")


def _usage_dict(u: LLMUsage) -> dict[str, Any]:
    return {k: v for k, v in asdict(u).items() if v is not None}
