"""Einrichtung über die Oberfläche (Einstellungen → Geräte & Dienste)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADMIN,
    CONF_ICON,
    CONF_TITLE,
    DEFAULT_ADMIN,
    DEFAULT_ICON,
    DEFAULT_TITLE,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_TITLE, default=defaults.get(CONF_TITLE, DEFAULT_TITLE)): str,
            vol.Required(CONF_ICON, default=defaults.get(CONF_ICON, DEFAULT_ICON)): selector.IconSelector(),
            vol.Required(CONF_ADMIN, default=defaults.get(CONF_ADMIN, DEFAULT_ADMIN)): bool,
        }
    )


class SamsungEhsPanelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Einmalige Einrichtung des Panels."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_TITLE], data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SamsungEhsPanelOptionsFlow()


class SamsungEhsPanelOptionsFlow(config_entries.OptionsFlow):
    """Titel, Symbol und Admin-Recht nachträglich ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
