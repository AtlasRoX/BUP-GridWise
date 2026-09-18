import asyncio
import logging
from typing import Optional
import httpx
from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger("gridwise.llm.client")

_client: Optional[AsyncOpenAI] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


def get_llm_client() -> AsyncOpenAI:
    global _client, _client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _client is None or _client_loop is None or _client_loop != current_loop or _client_loop.is_closed():
        api_key = settings.effective_api_key or "dummy-key-for-local-testing"
        base_url = settings.effective_base_url

        # Configure connection pool with bounded limits for Render
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=0,
        )
        _client_loop = current_loop
        logger.info(f"Initialized LLM client with base_url={base_url}, model={settings.effective_model}")
    return _client


async def close_llm_client() -> None:
    global _client, _client_loop
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
        _client = None
        _client_loop = None
