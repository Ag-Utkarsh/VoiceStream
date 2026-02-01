import pytest
import pytest_asyncio
import os
from dotenv import load_dotenv

load_dotenv('.env.test', override=True)

@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy for all tests"""
    import asyncio
    return asyncio.get_event_loop_policy()

@pytest_asyncio.fixture(scope="session")
async def event_loop(event_loop_policy):
    """Create a session-scoped event loop for all async tests"""
    import asyncio
    loop = event_loop_policy.new_event_loop()
    yield loop
    loop.close()