import asyncio
import os

# Use LiteLLM's bundled model cost map instead of fetching it over the network on import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import pytest


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the rate-limiting delays between probes."""
    async def _instant(_delay, *args, **kwargs):
        return None
    monkeypatch.setattr(asyncio, "sleep", _instant)
