import os

import pytest

from conversation_agent.llm.dashscope_client import DashScopeClient
from conversation_agent.llm.models import default_model_registry


pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def test_dashscope_qwen3_8b_smoke():
    if os.getenv("RUN_DASHSCOPE_INTEGRATION") != "1":
        pytest.skip("Set RUN_DASHSCOPE_INTEGRATION=1 to run the real smoke test")
    api_key = os.getenv("CONVAGENT_DASHSCOPE_INTEGRATION_API_KEY", "").strip()
    if not api_key:
        pytest.skip("Dedicated DashScope integration key is not configured")
    client = DashScopeClient(
        api_key=api_key,
        model_config=default_model_registry().standard,
    )
    response = client.call([{"role": "user", "content": "Reply with OK."}])
    assert response.text
    assert response.model
