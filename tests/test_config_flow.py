"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_CODE, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.newport_news_water.api import DEFAULT_USER_AGENT
from custom_components.newport_news_water.const import CONF_USER_AGENT, DOMAIN

from .portal import FAKE_CODE, FAKE_PASSWORD, FAKE_USERNAME, FakePortal

USER_INPUT = {CONF_USERNAME: FAKE_USERNAME, CONF_PASSWORD: FAKE_PASSWORD}
ENTRY_DATA = {**USER_INPUT, CONF_USER_AGENT: DEFAULT_USER_AGENT}


def _no_setup():
    return patch("custom_components.newport_news_water.async_setup_entry", return_value=True)


async def test_trusted_login(hass: HomeAssistant, portal: FakePortal) -> None:
    """When the portal trusts the device, no code is asked for."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with _no_setup():
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == FAKE_USERNAME
    assert portal.codes_sent == 0


async def test_login_with_code(hass: HomeAssistant, portal: FakePortal) -> None:
    """The emailed code is requested once, a wrong code is retried, then the entry is made."""
    portal.needs_code = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "verify"
    assert portal.codes_sent == 1

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "999999"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CODE: "invalid_code"}
    assert portal.codes_sent == 1  # no automatic resend

    with _no_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: FAKE_CODE}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA


async def test_invalid_auth(hass: HomeAssistant, portal: FakePortal) -> None:
    """A wrong password shows an error and can be corrected."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: FAKE_USERNAME, CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    with _no_setup():
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    """Portal errors show cannot_connect."""
    aioclient_mock.get("https://myservices.nnww.nnva.gov/NNWW/", status=503)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, portal: FakePortal) -> None:
    """The same account can't be added twice."""
    MockConfigEntry(domain=DOMAIN, unique_id=FAKE_USERNAME, data=ENTRY_DATA).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_USERNAME: FAKE_USERNAME.upper()}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_with_code(
    hass: HomeAssistant, portal: FakePortal, config_entry: MockConfigEntry
) -> None:
    """Reauth reuses the stored User-Agent and handles the code step."""
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_USER_AGENT: "Stored UA/1.0"}
    )
    portal.needs_code = True
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: FAKE_PASSWORD}
    )
    assert result["step_id"] == "verify"
    with _no_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: FAKE_CODE}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_USER_AGENT] == "Stored UA/1.0"
