import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.cli.check_ucm import check


def test_disabled_probe_makes_no_requests():
    runtime = Mock(api_configured=False, api_action=AsyncMock())
    assert asyncio.run(check(runtime)) == 2
    runtime.api_action.assert_not_awaited()


def test_probe_reports_only_counts(capsys):
    action = AsyncMock(side_effect=[
        {"response": {"channel": {"callerid": "private-number"}}},
        {"response": {"channel": []}},
    ])
    assert asyncio.run(check(Mock(api_configured=True, api_action=action))) == 0
    assert [call.args for call in action.await_args_list] == [
        ("listUnBridgedChannels",), ("listBridgedChannels",)
    ]
    assert capsys.readouterr().out.splitlines() == [
        "OK: listUnBridgedChannels: 1 channels", "OK: listBridgedChannels: 0 channels"
    ]


@pytest.mark.parametrize("response", [
    {}, {"response": {"channel": "private-number"}},
    httpx.ConnectError("private-host"), RuntimeError("private-token"),
])
def test_probe_fails_without_exposing_response(response, capsys):
    action = AsyncMock(side_effect=[response])
    assert asyncio.run(check(Mock(api_configured=True, api_action=action))) == 1
    assert "private" not in capsys.readouterr().out
    assert action.await_count == 1
