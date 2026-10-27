"""Client for the Newport News Waterworks customer portal (a SEW SmartCMobile site).

The portal is an ASP.NET WebForms app. Logging in is a JSON POST; when the portal
does not recognise the device (keyed on the User-Agent) it asks for a 6-digit code
sent by email before the session is usable. Usage data comes from the
``Usages.aspx/LoadWaterUsage`` page method, which needs the page's CSRF token.

Never log raw responses: they include the account holder's name, address and email.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
import json
import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://myservices.nnww.nnva.gov/NNWW/"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Each LoadWaterUsage call returns the 31 days ending on ``strDate``.
DAYS_PER_PAGE = 31

_CSRF_RE = re.compile(r'id="hdnCSRFToken"\s+value="([^"]*)"')
_TIMEOUT = aiohttp.ClientTimeout(total=60)
_EMAIL_CODE = 2  # authenticationType for email; 1 is SMS


class Unit(StrEnum):
    """Units the usage endpoint can report in."""

    GALLONS = "G"
    HCF = "W"
    DOLLARS = "D"


class NNWWError(Exception):
    """Base error for the portal client."""


class CannotConnect(NNWWError):
    """The portal could not be reached or returned something unexpected."""


class InvalidAuth(NNWWError):
    """The username or password was rejected."""


class VerificationRequired(NNWWError):
    """The portal wants an emailed code before the session can be used."""


class InvalidCode(NNWWError):
    """The emailed verification code was rejected."""


class SessionExpired(NNWWError):
    """The portal session is no longer logged in."""


@dataclass(frozen=True, slots=True)
class Meter:
    """A water meter on the account."""

    meter_number: str
    is_ami: bool


@dataclass(frozen=True, slots=True)
class DailyRead:
    """One day's reading."""

    day: date
    value: float


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """The portal's billing-period summary block."""

    so_far: float | None
    projected: float | None
    average: float | None
    highest: float | None


@dataclass(frozen=True, slots=True)
class UsagePage:
    """One LoadWaterUsage response."""

    reads: list[DailyRead]
    summary: UsageSummary | None


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _decode_d(payload: Any) -> Any:
    """Unwrap an ASP.NET page-method response (``{"d": "<json string>"}``)."""
    if not isinstance(payload, dict) or "d" not in payload:
        raise CannotConnect("Unexpected response from portal")
    inner = payload["d"]
    if inner == "SessionExpired":
        raise SessionExpired
    if isinstance(inner, str):
        if not inner:
            return None
        try:
            return json.loads(inner)
        except ValueError as err:
            raise CannotConnect("Portal returned malformed data") from err
    return inner


def _portal_message(data: Any) -> str | None:
    """Return the message from a ``dtResponse``/``dtException`` block, if any."""
    if isinstance(data, dict):
        for key, field in (("dtResponse", "Message"), ("dtException", "MessageInformation")):
            block = data.get(key)
            if block:
                return str(block[0].get(field) or "")
    return None


def parse_login(data: Any) -> None:
    """Interpret a validateLogin / verify response; raise if not logged in."""
    if isinstance(data, dict):
        table = data.get("Table")
        if table and ("MaskedEmail" in table[0] or "MaskedPhone" in table[0]):
            raise VerificationRequired
        message = _portal_message(data)
        raise InvalidAuth(message or "Login rejected")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise InvalidAuth("Login rejected")
    main = data[0]
    if str(main.get("STATUS")) == "0":
        raise InvalidAuth(main.get("Message") or "Login rejected")


def parse_usage(data: Any) -> UsagePage:
    """Parse a LoadWaterUsage response."""
    if data is None:
        return UsagePage([], None)
    if not isinstance(data, dict):
        raise CannotConnect("Unexpected usage response")
    reads: list[DailyRead] = []
    for row in data.get("objUsageGenerationResultSetTwo") or []:
        value = _num(row.get("UsageValue"))
        raw_day = row.get("UsageDate")
        if value is None or not raw_day:
            continue
        try:
            day = datetime.strptime(raw_day.strip(), "%B %d, %Y").date()
        except ValueError:
            continue
        reads.append(DailyRead(day, value))
    reads.sort(key=lambda r: r.day)

    summary = None
    if tentative := data.get("getTentativeData"):
        t = tentative[0]
        summary = UsageSummary(
            so_far=_num(t.get("SoFar")),
            projected=_num(t.get("ExpectedUsage")),
            average=_num(t.get("Average")),
            highest=_num(t.get("Highest")),
        )
    return UsagePage(reads, summary)


class NNWWClient:
    """Async client for the portal. Give it its own cookie jar per account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self._csrf: str | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "X-Requested-With": "XMLHttpRequest"}

    async def _post(self, path: str, body: dict[str, Any], csrf: bool = False) -> Any:
        headers = self._headers
        if csrf:
            headers |= {"csrftoken": self._csrf or "", "isajax": "1"}
        try:
            async with self._session.post(
                BASE_URL + path, json=body, headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise SessionExpired
                if resp.status >= 400:
                    raise CannotConnect(f"{path} returned HTTP {resp.status}")
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnect(f"Error contacting portal: {err}") from err
        except TimeoutError as err:
            raise CannotConnect("Timed out contacting portal") from err
        try:
            payload = json.loads(text)
        except ValueError as err:
            raise CannotConnect(f"{path} returned non-JSON") from err
        return _decode_d(payload)

    async def _get(self, path: str) -> tuple[str, str]:
        try:
            async with self._session.get(
                BASE_URL + path, headers={"User-Agent": self._user_agent}, timeout=_TIMEOUT
            ) as resp:
                if resp.status >= 400:
                    raise CannotConnect(f"{path} returned HTTP {resp.status}")
                return str(resp.url), await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnect(f"Error contacting portal: {err}") from err
        except TimeoutError as err:
            raise CannotConnect("Timed out contacting portal") from err

    async def login(self) -> None:
        """Log in. Raises VerificationRequired if an emailed code is needed."""
        self._csrf = None
        await self._get("")
        # The web page calls this first and ignores failures; so do we.
        with contextlib.suppress(NNWWError):
            await self._post("default.aspx/updateState", {})
        data = await self._post(
            "default.aspx/validateLogin",
            {
                "username": self._username,
                "password": self._password,
                "rememberme": False,
                "calledFrom": "LN",
                "ExternalLoginId": "",
                "LoginMode": "1",
                "utilityAcountNumber": "",
                "isEdgeBrowser": False,
            },
        )
        parse_login(data)

    async def send_verification_code(self) -> None:
        """Ask the portal to email a verification code. Call after VerificationRequired."""
        data = await self._post(
            "Authentication.aspx/CreateTwoFactorAuthenticationToken",
            {"authenticationType": _EMAIL_CODE, "isEdgeBrowser": False},
        )
        if (
            isinstance(data, dict)
            and (block := data.get("dtResponse"))
            and str(block[0].get("Status")) != "1"
        ):
            raise CannotConnect(block[0].get("Message") or "Could not send code")

    async def verify_code(self, code: str) -> None:
        """Submit the emailed code. Afterwards the portal trusts this User-Agent."""
        data = await self._post(
            "Authentication.aspx/VerifyTwoFactorAuthenticationTokenAsync",
            {"token": code.strip(), "authenticationType": _EMAIL_CODE, "isEdgeBrowser": False},
        )
        if isinstance(data, dict) and "dtResponse" in data:
            raise InvalidCode(_portal_message(data) or "Code rejected")
        parse_login(data)

    async def _ensure_csrf(self) -> None:
        if self._csrf:
            return
        url, html = await self._get("Usages.aspx")
        match = _CSRF_RE.search(html)
        if "usages.aspx" not in url.lower() or not match:
            raise SessionExpired
        self._csrf = match.group(1)

    async def _page_method(self, path: str, body: dict[str, Any]) -> Any:
        """Call a Usages.aspx page method, refreshing the CSRF token once if needed."""
        for attempt in range(2):
            await self._ensure_csrf()
            data = await self._post(path, body, csrf=True)
            if isinstance(data, dict) and "dtException" in data:
                message = _portal_message(data) or ""
                if attempt == 0 and "csrf" in message.lower():
                    self._csrf = None
                    continue
                raise CannotConnect(message or f"{path} failed")
            return data
        raise CannotConnect(f"{path} failed")  # pragma: no cover

    async def get_meters(self) -> list[Meter]:
        """Return the water meters on the account."""
        data = await self._page_method("Usages.aspx/BindMultiMeter", {"MeterType": "W"})
        rows = (data.get("Data") or []) if isinstance(data, dict) else []
        return [
            Meter(str(row["MeterNumber"]), bool(row.get("IsAMI")))
            for row in rows
            if row.get("MeterNumber")
        ]

    async def get_daily(
        self, unit: Unit, end: date | None = None, meter_number: str = ""
    ) -> UsagePage:
        """Return up to 31 daily reads ending on ``end`` (default: most recent)."""
        data = await self._page_method(
            "Usages.aspx/LoadWaterUsage",
            {
                "Type": str(unit),
                "Mode": "D",
                "strDate": end.strftime("%m/%d/%Y") if end else "",
                "hourlyType": "H",
                "seasonId": "",
                "weatherOverlay": 0,
                "usageyear": "",
                "MeterNumber": meter_number,
                "DateFromDaily": "",
                "DateToDaily": "",
                "isNoDashboard": True,
            },
        )
        return parse_usage(data)

    async def get_daily_history(
        self, unit: Unit, start: date, end: date, meter_number: str = ""
    ) -> tuple[dict[date, float], UsageSummary | None]:
        """Return daily reads from ``start`` to ``end`` inclusive, paging backwards.

        Also returns the billing-period summary from the newest page. Stops early
        when the portal returns an empty page (no older history).
        """
        result: dict[date, float] = {}
        summary: UsageSummary | None = None
        cursor = end
        while cursor >= start:
            page = await self.get_daily(unit, cursor, meter_number)
            if summary is None:
                summary = page.summary
            if not page.reads:
                break
            for read in page.reads:
                if start <= read.day <= end:
                    result[read.day] = read.value
            cursor = min(
                cursor - timedelta(days=DAYS_PER_PAGE), page.reads[0].day - timedelta(days=1)
            )
        return result, summary
