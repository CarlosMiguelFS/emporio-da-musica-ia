import pytest

from emporio import agent, tools


@pytest.fixture(autouse=True)
def reset_limits():
    # the limits count the whole server, so one test can't leave them full for the next
    agent.recent_messages.clear()
    tools.shared_attempts['failed'] = 0
    tools.order_fails.clear()
    tools.server_fails.clear()
