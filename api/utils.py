"""Shared utilities for the Lab Analysis Platform API."""
from __future__ import annotations

import asyncio
import logging

import anthropic

logger = logging.getLogger(__name__)


async def call_claude_with_retry(
    client: anthropic.AsyncAnthropic,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 20,
    **create_kwargs,
):
    """Call client.messages.create() with retry on 529 / InternalServerError.

    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with client.messages.stream(**create_kwargs) as stream:
                return await stream.get_final_message()
        except (anthropic.InternalServerError, anthropic.APIStatusError) as exc:
            is_overloaded = (
                isinstance(exc, anthropic.InternalServerError)
                or getattr(exc, "status_code", None) == 529
            )
            if is_overloaded and attempt < max_attempts:
                last_exc = exc
                logger.info(
                    "Anthropic API overloaded (attempt %d/%d) — retrying in %gs",
                    attempt,
                    max_attempts,
                    backoff_seconds,
                )
                await asyncio.sleep(backoff_seconds)
            else:
                raise
    raise last_exc  # type: ignore[misc]
