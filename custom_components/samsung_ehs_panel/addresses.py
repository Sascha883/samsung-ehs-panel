"""Adressen von Innen-/Außengerät ermitteln (ESPHome-Erkennung und EHS Sentinel) und prüfen."""
from __future__ import annotations

import re
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import DOMAIN

EHS_DOMAIN = "ehs_sentinel"
NOTIFY_ID = f"{DOMAIN}_address"
ESP_NAMES = {
    "in_found": "nasa innengerät erkannt",
    "out_found": "nasa außengerät erkannt",
    "in_conf": "nasa innengerät konfiguriert",
    "out_conf": "nasa außengerät konfiguriert",
}
ADDR_RE = re.compile(r"^[0-9a-f]{2}\.[0-9a-f]{2}\.[0-9a-f]{2}$", re.I)


def _fmt(addr) -> str | None:
    if isinstance(addr, dict) and "class" in addr:
        try:
            return f"{int(addr['class']):02x}.{int(addr.get('channel', 0)):02x}.{int(addr.get('address', 0)):02x}"
        except (TypeError, ValueError):
            return None
    if isinstance(addr, str) and ADDR_RE.match(addr):
        return addr.lower()
    return None


@callback
def get_addresses(hass: HomeAssistant) -> dict:
    """ESPHome: erkannte + konfigurierte Adressen; EHS Sentinel: automatisch erkannte Adressen."""
    esp: dict[str, str | None] = {k: None for k in ESP_NAMES}
    for st in hass.states.async_all("sensor"):
        fn = str(st.attributes.get("friendly_name", "")).lower()
        for key, name in ESP_NAMES.items():
            if fn == name or fn.endswith(" " + name):
                esp[key] = _fmt(st.state)
    ehs = []
    coords = hass.data.get(EHS_DOMAIN, {}) or {}
    if isinstance(coords, dict):
        for entry_id, c in coords.items():
            if c is None or not hasattr(c, "indoor_address"):
                continue
            ehs.append({"entry_id": entry_id, "indoor": _fmt(getattr(c, "indoor_address", None)),
                        "outdoor": _fmt(getattr(c, "outdoor_address", None))})
    found_in = esp["in_found"] or next((e["indoor"] for e in ehs if e["indoor"]), None)
    found_out = esp["out_found"] or next((e["outdoor"] for e in ehs if e["outdoor"]), None)
    mismatch = []
    if esp["in_conf"] and found_in and esp["in_conf"] != found_in:
        mismatch.append(("Innengerät", esp["in_conf"], found_in))
    if esp["out_conf"] and found_out and esp["out_conf"] != found_out:
        mismatch.append(("Außengerät", esp["out_conf"], found_out))
    return {"esp": esp, "ehs": ehs, "indoor": found_in, "outdoor": found_out,
            "mismatch": [{"unit": u, "configured": c, "detected": d} for u, c, d in mismatch]}


def yaml_with_addresses(text: str, addr: dict) -> str:
    """Setzt die erkannten Adressen in die substitutions der ESPHome-YAML ein."""
    if addr.get("indoor"):
        text = re.sub(r'(\n\s*indoor_address:\s*)"[^"]*"', rf'\g<1>"{addr["indoor"]}"', text, count=1)
    if addr.get("outdoor"):
        text = re.sub(r'(\n\s*outdoor_address:\s*)"[^"]*"', rf'\g<1>"{addr["outdoor"]}"', text, count=1)
    return text


@callback
def async_check(hass: HomeAssistant, *_args) -> None:
    """Automatische Prüfung: falsche Adresse → Benachrichtigung in HA, sonst Benachrichtigung entfernen."""
    a = get_addresses(hass)
    if a["mismatch"]:
        lines = "\n".join(f"- **{m['unit']}**: konfiguriert `{m['configured']}`, am Bus erkannt `{m['detected']}`" for m in a["mismatch"])
        persistent_notification.async_create(
            hass,
            f"Die ESPHome-Konfiguration der Samsung-WP nutzt eine falsche Geräteadresse:\n\n{lines}\n\n"
            "Abhilfe: **EHS Panel → Übersicht → ESPHome-YAML herunterladen** (enthält die erkannten Adressen) "
            "und den ESP neu flashen – oder in der YAML unter `substitutions` die Adresse ändern.",
            title="Samsung EHS Panel: Adresse prüfen",
            notification_id=NOTIFY_ID,
        )
    else:
        persistent_notification.async_dismiss(hass, NOTIFY_ID)


def async_start(hass: HomeAssistant):
    """Prüfung 2 min nach dem Start und danach alle 10 min. Gibt Abmeldefunktionen zurück."""
    run = callback(lambda _now: async_check(hass))
    return [
        async_call_later(hass, 120, run),
        async_track_time_interval(hass, run, timedelta(minutes=10)),
    ]
