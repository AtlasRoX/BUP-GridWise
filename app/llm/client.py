import logging
from typing import Optional
import httpx
from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger("gridwise.llm.client")

_client: Optional[AsyncOpenAI] = None


def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is None:
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
        )
        logger.info(f"Initialized LLM client with base_url={base_url}, model={settings.effective_model}")
    return _client


async def close_llm_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
