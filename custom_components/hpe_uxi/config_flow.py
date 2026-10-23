"""Set up HPE UXI in the UI: a GreenLake personal API client for the UXI service, then how often to check."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import client_for
from .api import UxiAuthError, UxiError, clean_base_url
from .const import (
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SCAN,
    CONF_TENANT_ID,
    DEFAULT_BASE_URL,
    DOMAIN,
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
    POLL_SECONDS,
)


class HpeUxiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HpeUxiOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_CLIENT_ID: user_input[CONF_CLIENT_ID].strip(),
                CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET].strip(),
                CONF_BASE_URL: clean_base_url(user_input.get(CONF_BASE_URL) or DEFAULT_BASE_URL),
            }
            client = client_for(self.hass, data)
            try:
                await client.sensors()
            except UxiAuthError:
                errors["base"] = "invalid_auth"
            except UxiError:
                errors["base"] = "cannot_connect"
            else:
                tenant = client.tenant_id or data[CONF_CLIENT_ID]
                await self.async_set_unique_id(tenant)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="HPE UXI", data={**data, CONF_TENANT_ID: tenant}, options={CONF_SCAN: POLL_SECONDS}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                }
            ),
            errors=errors,
        )


class HpeUxiOptionsFlow(OptionsFlow):
    """How often UXI is checked."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_SCAN: int(user_input[CONF_SCAN])})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN, default=self.config_entry.options.get(CONF_SCAN, POLL_SECONDS)
                    ): NumberSelector(NumberSelectorConfig(min=MIN_POLL_SECONDS, max=MAX_POLL_SECONDS, step=5,
                                                           unit_of_measurement="s", mode=NumberSelectorMode.BOX)),
                }
            ),
        )
