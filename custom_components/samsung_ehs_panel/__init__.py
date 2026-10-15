"""Samsung EHS Panel – Bedieneinheit (HMI-800-Menü) für EHS Sentinel als Seitenleisten-Panel."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, persistent_notification
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

try:
    from homeassistant.helpers.http import KEY_HASS
except ImportError:  # ältere HA-Versionen
    from homeassistant.components.http import KEY_HASS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import addresses as panel_addr
from . import logfilter as panel_logfilter
from . import websocket as panel_ws
from .logbuffer import EhsLogBuffer

from .const import (
    CONF_ADMIN,
    CONF_ICON,
    CONF_MANAGER,
    CONF_TITLE,
    DEFAULT_ADMIN,
    DEFAULT_ICON,
    DEFAULT_MANAGER,
    DEFAULT_TITLE,
    DOMAIN,
    MANAGER_FILE,
    MANAGER_URL_PATH,
    PANEL_FILE,
    PANEL_URL_PATH,
    STATIC_URL,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).parent


def _panel_file() -> Path:
    """Panel-Datei direkt im Integrationsordner oder im Unterordner www/."""
    for p in (BASE_DIR / PANEL_FILE, BASE_DIR / "www" / PANEL_FILE):
        if p.is_file():
            return p
    return BASE_DIR / PANEL_FILE


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Panel-Datei bereitstellen und Seitenleisten-Eintrag anlegen."""
    data = hass.data.setdefault(DOMAIN, {})

    # Selbsttest: fehlt die Panel-Datei, zeigt HA sonst nur „404: Not Found“
    panel_file = await hass.async_add_executor_job(_panel_file)
    if not await hass.async_add_executor_job(panel_file.is_file):
        msg = (
            f"Die Panel-Datei fehlt: `{panel_file}`.\n\n"
            f"`{PANEL_FILE}` muss direkt im Ordner `custom_components/{DOMAIN}/` liegen "
            "(oder im Unterordner `www`). Danach Seite neu laden."
        )
        _LOGGER.error(msg)
        persistent_notification.async_create(hass, msg, title="Samsung EHS Panel", notification_id=f"{DOMAIN}_missing")
    else:
        persistent_notification.async_dismiss(hass, f"{DOMAIN}_missing")

    # Panel-Datei über eine eigene View ausliefern (zeigt bei Problemen eine Meldung statt „404“)
    if not data.get("view_registered"):
        hass.http.register_view(PanelFileView(BASE_DIR))
        data["view_registered"] = True

    # Protokoll von EHS Sentinel mitschreiben + WebSocket-Befehle (einmal pro HA-Start)
    if "logbuffer" not in data:
        data["logbuffer"] = EhsLogBuffer()
    data["logbuffer"].attach()
    if not data.get("ws_registered"):
        panel_ws.async_register(hass)
        data["ws_registered"] = True

    # Log-Flut von EHS Sentinel bei 0xFF-Werten (Bauteil nicht vorhanden) ausblenden
    _flt = panel_logfilter.install()
    hass.data.setdefault(DOMAIN, {})["ff_filter"] = _flt
    entry.async_on_unload(lambda: panel_logfilter.uninstall(_flt))

    # Automatische Prüfung der Innen-/Außengeräte-Adressen (ESPHome)
    for unsub in panel_addr.async_start(hass):
        entry.async_on_unload(unsub)

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
    # Zweiter Eintrag: Verwaltung der EHS-Sentinel-Schnittstelle (nur Admins)
    if opts.get(CONF_MANAGER, DEFAULT_MANAGER):
        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="EHS Panel",
            sidebar_icon="mdi:lan-connect",
            frontend_url_path=MANAGER_URL_PATH,
            config={"url": f"{STATIC_URL}/{MANAGER_FILE}?v={VERSION}"},
            require_admin=True,
            update=True,
        )
    else:
        frontend.async_remove_panel(hass, MANAGER_URL_PATH)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.debug("Samsung EHS Panel %s unter /%s registriert", VERSION, PANEL_URL_PATH)
    return True


class PanelFileView(HomeAssistantView):
    """Liefert die Panel-Datei unter /<domain>/<datei>."""

    url = STATIC_URL + "/{filename}"
    name = f"{DOMAIN}:file"
    requires_auth = False  # das Panel-Iframe lädt ohne Auth-Header; die Daten holt das Panel mit HA-Anmeldung

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        if "/" in filename or "\\" in filename or filename.startswith("."):
            return web.Response(status=400, text="Ungültiger Dateiname")
        if not filename.lower().endswith((".html", ".js", ".css", ".png", ".svg", ".json", ".yaml")) or filename in ("manifest.json", "strings.json"):
            return web.Response(status=404, text="Nicht gefunden")
        hass = request.app[KEY_HASS]
        def _find():
            for p in (self._folder / filename, self._folder / "www" / filename):
                if p.is_file():
                    return p
            return None
        path = await hass.async_add_executor_job(_find) or (self._folder / filename)
        if not await hass.async_add_executor_job(path.is_file):
            listing = await hass.async_add_executor_job(lambda: sorted(p.name for p in self._folder.iterdir()))
            text = (
                "Samsung EHS Panel: Datei nicht gefunden\n\n"
                f"Gesucht: {path}\n"
                f"Inhalt: {', '.join(listing) or '(leer)'}\n\n"
                "Lösung: Datei samsung-ehs-hmi800.html in diesen Ordner legen und Seite neu laden."
            )
            return web.Response(status=404, text=text, content_type="text/plain", charset="utf-8")
        if filename.lower().endswith(".yaml"):
            text = await hass.async_add_executor_job(path.read_text, "utf-8")
            text = panel_addr.yaml_with_addresses(text, panel_addr.get_addresses(hass))
            return web.Response(text=text, content_type="text/yaml", charset="utf-8",
                                headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-cache"})
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Seitenleisten-Eintrag entfernen."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    frontend.async_remove_panel(hass, MANAGER_URL_PATH)
    buf = hass.data.get(DOMAIN, {}).get("logbuffer")
    if buf:
        buf.detach()
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Nach Änderung der Optionen neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)
