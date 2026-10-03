import json

import pytest

from tests.adapters.conformance import assert_adapter_conforms
from tests.adapters.fake_adapter import FakeAdapter


@pytest.mark.proxy
def test_fake_adapter_conforms() -> None:
    request = {
        "model": "fake-model",
        "stream": False,
        "system": "Be concise.",
        "tools": [{"name": "lookup"}],
        "messages": [{"role": "user", "content": "Hello"}],
    }
    plain_response = {"input_tokens": 10, "output_tokens": 4}
    tool_call_response = {"input_tokens": 10, "output_tokens": 4, "tool_call": {"name": "lookup"}}

    assert_adapter_conforms(
        FakeAdapter(),
        sample_raw_request=json.dumps(request).encode(),
        sample_headers={"x-api-key": "test-key"},
        plain_response=plain_response,
        tool_call_response=tool_call_response,
    )
