"""Samsung EHS Panel – Bedieneinheit (HMI-800-Menü) für EHS Sentinel als Seitenleisten-Panel."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, persistent_notification
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import websocket as panel_ws
from .logbuffer import EhsLogBuffer

from .const import (
    CONF_ADMIN,
    CONF_ICON,
    CONF_TITLE,
    DEFAULT_ADMIN,
    DEFAULT_ICON,
    DEFAULT_TITLE,
    DOMAIN,
    PANEL_FILE,
    PANEL_URL_PATH,
    STATIC_URL,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)
WWW_DIR = Path(__file__).parent / "www"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Panel-Datei bereitstellen und Seitenleisten-Eintrag anlegen."""
    data = hass.data.setdefault(DOMAIN, {})

    # Selbsttest: fehlt die Panel-Datei, zeigt HA sonst nur „404: Not Found“
    panel_file = WWW_DIR / PANEL_FILE
    if not await hass.async_add_executor_job(panel_file.is_file):
        msg = (
            f"Die Panel-Datei fehlt: `{panel_file}`.\n\n"
            "Bitte prüfen, ob im GitHub-Repository der Ordner "
            "`custom_components/samsung_ehs_panel/www/` mit `samsung-ehs-hmi800.html` vorhanden ist, "
            "und die Integration in HACS erneut herunterladen."
        )
        _LOGGER.error(msg)
        persistent_notification.async_create(hass, msg, title="Samsung EHS Panel", notification_id=f"{DOMAIN}_missing")
    else:
        persistent_notification.async_dismiss(hass, f"{DOMAIN}_missing")

    # Statischer Pfad lässt sich nicht wieder abmelden → nur einmal pro HA-Start registrieren
    if not data.get("static_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(WWW_DIR), cache_headers=False)]
        )
        data["static_registered"] = True

    # Protokoll von EHS Sentinel mitschreiben + WebSocket-Befehle (einmal pro HA-Start)
    if "logbuffer" not in data:
        data["logbuffer"] = EhsLogBuffer()
    data["logbuffer"].attach()
    if not data.get("ws_registered"):
        panel_ws.async_register(hass)
        data["ws_registered"] = True

    opts = {**entry.data, **entry.options}
    frontend.async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title=opts.get(CONF_TITLE, DEFAULT_TITLE),
        sidebar_icon=opts.get(CONF_ICON, DEFAULT_ICON),
        frontend_url_path=PANEL_URL_PATH,
        config={"url": f"{STATIC_URL}/{PANEL_FILE}?v={VERSION}"},
        require_admin=opts.get(CONF_ADMIN, DEFAULT_ADMIN),
        update=True,
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.debug("Samsung EHS Panel %s unter /%s registriert", VERSION, PANEL_URL_PATH)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Seitenleisten-Eintrag entfernen."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    buf = hass.data.get(DOMAIN, {}).get("logbuffer")
    if buf:
        buf.detach()
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Nach Änderung der Optionen neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)
