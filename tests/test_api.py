"""Tests for the portal client."""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.newport_news_water.api import (
    BASE_URL,
    CannotConnect,
    InvalidAuth,
    InvalidCode,
    NNWWClient,
    SessionExpired,
    Unit,
    VerificationRequired,
    parse_login,
    parse_usage,
)

from .conftest import TODAY
from .portal import (
    CSRF_ERROR,
    FAKE_CODE,
    FAKE_METER,
    FAKE_PASSWORD,
    FAKE_USERNAME,
    LOGIN_BAD_PASSWORD,
    LOGIN_NEEDS_CODE,
    LOGIN_OK,
    USAGES_HTML,
    FakePortal,
    wrap,
)


def _client(
    hass: HomeAssistant, mock: AiohttpClientMocker, password: str = FAKE_PASSWORD
) -> NNWWClient:
    return NNWWClient(async_create_clientsession(hass), FAKE_USERNAME, password)


def test_parse_login() -> None:
    """Login responses map to the right outcome."""
    parse_login(LOGIN_OK)
    with pytest.raises(VerificationRequired):
        parse_login(LOGIN_NEEDS_CODE)
    with pytest.raises(InvalidAuth, match="Invalid user name"):
        parse_login(LOGIN_BAD_PASSWORD)
    with pytest.raises(InvalidAuth):
        parse_login({"dtResponse": [{"Status": "0", "Message": "Locked"}]})
    with pytest.raises(InvalidAuth):
        parse_login(None)


def test_parse_usage_skips_bad_rows() -> None:
    """Rows without a value or with an unparseable date are skipped, rest sorted."""
    page = parse_usage(
        {
            "objUsageGenerationResultSetTwo": [
                {"UsageDate": "March 02, 2026", "UsageValue": 2.5},
                {"UsageDate": "March 01, 2026", "UsageValue": "1.25"},
                {"UsageDate": "not a date", "UsageValue": 3},
                {"UsageDate": "March 03, 2026", "UsageValue": None},
            ],
            "getTentativeData": [{"SoFar": "10.5", "ExpectedUsage": "2673.0800000000000001"}],
        }
    )
    assert [(r.day, r.value) for r in page.reads] == [
        (date(2026, 3, 1), 1.25),
        (date(2026, 3, 2), 2.5),
    ]
    assert page.summary.so_far == 10.5
    assert page.summary.projected == 2673.08
    assert page.summary.average is None
    assert parse_usage(None).reads == []


async def test_login_and_usage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, portal: FakePortal
) -> None:
    """A trusted login can read meters and page back through history."""
    client = _client(hass, aioclient_mock)
    await client.login()
    meters = await client.get_meters()
    assert [m.meter_number for m in meters] == [FAKE_METER]

    yesterday = TODAY - timedelta(days=1)
    gallons, summary = await client.get_daily_history(
        Unit.GALLONS, yesterday - timedelta(days=200), yesterday, FAKE_METER
    )
    assert gallons == portal.history
    assert summary.so_far == 1000
    # 90 days of history: three full pages, then an empty one stops paging.
    assert [c["strDate"] for c in portal.usage_calls] == [
        "03/14/2026",
        "02/11/2026",
        "01/11/2026",
        "12/11/2025",
    ]
    assert {c["MeterNumber"] for c in portal.usage_calls} == {FAKE_METER}

    # The CSRF token from Usages.aspx is sent with page-method calls.
    usage_headers = [
        headers
        for method, url, _, headers in aioclient_mock.mock_calls
        if str(url).endswith("LoadWaterUsage")
    ]
    assert usage_headers[0]["csrftoken"] == "fake-csrf-token"


async def test_history_respects_start(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, portal: FakePortal
) -> None:
    """Only days inside the requested range are returned."""
    client = _client(hass, aioclient_mock)
    await client.login()
    end = TODAY - timedelta(days=1)
    start = end - timedelta(days=9)
    cost, _ = await client.get_daily_history(Unit.DOLLARS, start, end)
    assert sorted(cost) == [start + timedelta(days=i) for i in range(10)]
    assert len(portal.usage_calls) == 1


async def test_verification_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, portal: FakePortal
) -> None:
    """An untrusted login needs the emailed code."""
    portal.needs_code = True
    client = _client(hass, aioclient_mock)
    with pytest.raises(VerificationRequired):
        await client.login()
    await client.send_verification_code()
    assert portal.codes_sent == 1
    with pytest.raises(InvalidCode):
        await client.verify_code("000000")
    await client.verify_code(f" {FAKE_CODE} ")
    await client.login()


async def test_bad_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, portal: FakePortal
) -> None:
    """A wrong password raises InvalidAuth."""
    with pytest.raises(InvalidAuth):
        await _client(hass, aioclient_mock, "wrong").login()


async def test_csrf_refreshed_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, portal: FakePortal
) -> None:
    """A CSRF error refetches the token and retries once, then gives up."""
    client = _client(hass, aioclient_mock)
    await client.login()
    responses = [CSRF_ERROR, {"Status": 1, "Data": []}]

    async def side_effect(method, url, data):
        return AiohttpClientMockResponse(method, url, json=wrap(responses.pop(0)))

    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL + "Usages.aspx", text=USAGES_HTML)
    aioclient_mock.post(BASE_URL + "Usages.aspx/BindMultiMeter", side_effect=side_effect)
    assert await client.get_meters() == []
    usages_gets = [c for c in aioclient_mock.mock_calls if c[0].lower() == "get"]
    assert len(usages_gets) == 2  # initial token + refresh

    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL + "Usages.aspx", text=USAGES_HTML)
    aioclient_mock.post(BASE_URL + "Usages.aspx/BindMultiMeter", json=wrap(CSRF_ERROR))
    with pytest.raises(CannotConnect, match="CSRF"):
        await client.get_meters()


async def test_session_expired(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Being bounced off Usages.aspx or a SessionExpired reply raises SessionExpired."""
    client = _client(hass, aioclient_mock)
    aioclient_mock.get(BASE_URL + "Usages.aspx", text="<html>login page</html>")
    with pytest.raises(SessionExpired):
        await client.get_meters()

    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL, text="")
    aioclient_mock.post(BASE_URL + "default.aspx/updateState", json={"d": ""})
    aioclient_mock.post(BASE_URL + "default.aspx/validateLogin", json={"d": "SessionExpired"})
    with pytest.raises(SessionExpired):
        await client.login()


async def test_http_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """HTTP errors and non-JSON bodies raise CannotConnect."""
    client = _client(hass, aioclient_mock)
    aioclient_mock.get(BASE_URL, status=500)
    with pytest.raises(CannotConnect):
        await client.login()

    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL, text="")
    aioclient_mock.post(BASE_URL + "default.aspx/updateState", json={"d": ""})
    aioclient_mock.post(BASE_URL + "default.aspx/validateLogin", text="<html>oops</html>")
    with pytest.raises(CannotConnect):
        await client.login()
