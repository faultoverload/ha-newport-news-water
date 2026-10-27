"""A fake NNWW portal for tests. All account data here is made up."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.newport_news_water.api import BASE_URL

FAKE_USERNAME = "test.user@example.com"
FAKE_PASSWORD = "not-a-real-password"
FAKE_METER = "0000012345"
FAKE_CODE = "123456"
FAKE_CSRF = "fake-csrf-token"
COST_PER_GALLON = 0.1337

LOGIN_OK = [
    {
        "UserName": FAKE_USERNAME,
        "Name": "Test User",
        "Address": "1 Example St",
        "AccountNumber": "1",
        "STATUS": "1",
        "Message": "",
        "DashboardOption": "1",
        "IsFirstLogin": "",
    },
    {"CoulmnCount": 5},
]
LOGIN_BAD_PASSWORD = [
    {"STATUS": "0", "Message": "Invalid user name or password.", "IsFirstLogin": ""},
    {"CoulmnCount": 2},
]
LOGIN_NEEDS_CODE = {"Table": [{"MaskedPhone": None, "MaskedEmail": "t***@example.com"}]}
CODE_SENT = {"dtResponse": [{"Status": "1", "Message": "Authentication code has been sent."}]}
CODE_WRONG = {
    "dtResponse": [
        {
            "Status": "0",
            "Message": "The entered authentication code is not correct.",
        }
    ]
}
CSRF_ERROR = {"dtException": [{"StatusCode": "0", "MessageInformation": "Invalid CSRF Token"}]}

USAGES_HTML = (
    '<html><input type="hidden" name="ctl00$hdnCSRFToken" '
    f'id="hdnCSRFToken" value="{FAKE_CSRF}" /></html>'
)


def wrap(data: Any) -> dict[str, str]:
    """Wrap data like an ASP.NET page method."""
    return {"d": data if isinstance(data, str) else json.dumps(data)}


def usage_response(
    history: dict[date, float], end: date, unit: str, today: date
) -> dict[str, Any] | None:
    """Build a LoadWaterUsage payload: the 31 days ending on ``end``."""
    rows = []
    for offset in range(31):
        day = end - timedelta(days=offset)
        if day == today:
            value = 0.0  # the portal shows a 0 placeholder for today
        elif day in history:
            value = history[day] * (COST_PER_GALLON if unit == "D" else 1)
        else:
            continue
        label = day.strftime("%B %d, %Y")
        rows.append(
            {
                "HighUsage": 0.0,
                "UsageDate": label,
                "DateOfReading": label,
                "UsageValue": round(value, 2),
                "DemandValue": "",
                "Skey": None,
                "UsageColorCode": "#0000FF",
                "DemandColorCode": "",
                "WeatherUsageDate": label,
            }
        )
    if not rows:
        return {
            "objUsageGenerationResultSetTwo": [],
            "objUsageGenerationResultSetThree": [],
            "getTentativeData": [],
        }
    factor = COST_PER_GALLON if unit == "D" else 1
    return {
        "objUsageGenerationResultSetOne": [{"CityName": "Newport News"}],
        "objUsageGenerationResultSetTwo": rows,
        "objUsageGenerationResultSetThree": [{"FromDate": "", "ToDate": ""}],
        "listUsageColor": [],
        "getTentativeData": [
            {
                "SoFar": str(round(1000 * factor, 2)),
                "ExpectedUsage": str(round(2000 * factor, 2)),
                "Average": str(round(50 * factor, 2)),
                "Highest": str(round(100 * factor, 2)),
            }
        ],
    }


@dataclass
class FakePortal:
    """Registers portal endpoints on an AiohttpClientMocker."""

    history: dict[date, float]
    today: date
    needs_code: bool = False
    bad_password: bool = False
    usage_calls: list[dict[str, Any]] = field(default_factory=list)
    codes_sent: int = 0

    def register(self, mock: AiohttpClientMocker) -> None:
        """Register all endpoints."""
        mock.clear_requests()
        mock.get(BASE_URL, text="<html></html>")
        mock.post(BASE_URL + "default.aspx/updateState", json={"d": ""})
        mock.post(BASE_URL + "default.aspx/validateLogin", side_effect=self._login)
        mock.post(
            BASE_URL + "Authentication.aspx/CreateTwoFactorAuthenticationToken",
            side_effect=self._send_code,
        )
        mock.post(
            BASE_URL + "Authentication.aspx/VerifyTwoFactorAuthenticationTokenAsync",
            side_effect=self._verify,
        )
        mock.get(BASE_URL + "Usages.aspx", text=USAGES_HTML)
        mock.post(
            BASE_URL + "Usages.aspx/BindMultiMeter",
            json=wrap(
                {
                    "Status": 1,
                    "Message": None,
                    "Data": [
                        {
                            "Mode": None,
                            "AccountNumber": 1,
                            "MeterNumber": FAKE_METER,
                            "IsAMI": True,
                            "UtilityAccountNumber": "000000000000",
                        }
                    ],
                }
            ),
        )
        mock.post(BASE_URL + "Usages.aspx/LoadWaterUsage", side_effect=self._usage)

    @staticmethod
    def _resp(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=wrap(data))

    async def _login(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        if self.bad_password or data["password"] != FAKE_PASSWORD:
            return self._resp(method, url, LOGIN_BAD_PASSWORD)
        if self.needs_code:
            return self._resp(method, url, LOGIN_NEEDS_CODE)
        return self._resp(method, url, LOGIN_OK)

    async def _send_code(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        self.codes_sent += 1
        return self._resp(method, url, CODE_SENT)

    async def _verify(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        if data["token"] != FAKE_CODE:
            return self._resp(method, url, CODE_WRONG)
        self.needs_code = False
        return self._resp(method, url, LOGIN_OK)

    async def _usage(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        self.usage_calls.append(data)
        end = (
            datetime.strptime(data["strDate"], "%m/%d/%Y").date() if data["strDate"] else self.today
        )
        return self._resp(method, url, usage_response(self.history, end, data["Type"], self.today))
