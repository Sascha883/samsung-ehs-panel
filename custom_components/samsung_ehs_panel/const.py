"""Konstanten für das Samsung EHS Panel."""
from pathlib import Path

# Domain = Ordnername (custom_components/<ordner>) – so passt es auch nach Umbenennen
DOMAIN = Path(__file__).resolve().parent.name
WS_PREFIX = "samsung_ehs_panel"  # WebSocket-Befehle (fest, das Panel ruft diese Namen auf)
VERSION = "1.2.0"

PANEL_FILE = "samsung-ehs-hmi800.html"
STATIC_URL = f"/{DOMAIN}"
PANEL_URL_PATH = "samsung-ehs"
MANAGER_FILE = "ehs-sentinel-manager.html"
MANAGER_URL_PATH = "ehs-sentinel"

CONF_TITLE = "sidebar_title"
CONF_ICON = "sidebar_icon"
CONF_ADMIN = "require_admin"
CONF_MANAGER = "show_manager"

DEFAULT_TITLE = "Wärmepumpe"
DEFAULT_ICON = "mdi:heat-pump"
DEFAULT_ADMIN = False
DEFAULT_MANAGER = True
