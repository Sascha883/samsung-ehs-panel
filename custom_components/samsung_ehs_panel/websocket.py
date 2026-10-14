"""WebSocket-Befehle des Panels: Verbindungsstatus und Protokolle von EHS Sentinel."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN, VERSION, WS_PREFIX

EHS_DOMAIN = "ehs_sentinel"
LEVELS = {"all": 0, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def _buffer(hass: HomeAssistant):
    return hass.data.get(DOMAIN, {}).get("logbuffer")


def _instances(hass: HomeAssistant) -> list[dict[str, Any]]:
    coords = hass.data.get(EHS_DOMAIN, {}) or {}
    out = []
    for entry in hass.config_entries.async_entries(EHS_DOMAIN):
        c = coords.get(entry.entry_id) if isinstance(coords, dict) else None
        stats = {}
        queue = None
        if c is not None:
            try:
                stats = dict(getattr(c, "stats", {}) or {})
            except Exception:  # pylint: disable=broad-except
                stats = {}
            q = getattr(c, "_packet_queue", None)
            try:
                queue = q.qsize() if q is not None else None
            except Exception:  # pylint: disable=broad-except
                queue = None
        out.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": str(entry.state.value if hasattr(entry.state, "value") else entry.state),
                "loaded": c is not None,
                "connected": bool(c is not None and getattr(c, "_tcp_reader", None) is not None),
                "running": bool(getattr(c, "running", False)) if c is not None else False,
                "ip": getattr(c, "ip", None) if c is not None else (entry.options.get("ip") or entry.data.get("ip")),
                "port": getattr(c, "port", None) if c is not None else (entry.options.get("port") or entry.data.get("port")),
                "write_mode": getattr(c, "writemode", None) if c is not None else None,
                "polling": getattr(c, "polling", None) if c is not None else None,
                "extended_logging": getattr(c, "extended_logging", None) if c is not None else None,
                "stats": stats,
                "queue": queue,
            }
        )
    return out


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/status"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_status(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    try:
        ehs_version = (await async_get_integration(hass, EHS_DOMAIN)).version
        ehs_version = str(ehs_version) if ehs_version else None
    except Exception:  # pylint: disable=broad-except
        ehs_version = None
    connection.send_result(
        msg["id"],
        {
            "version": VERSION,
            "ehs_version": ehs_version,
            "instances": _instances(hass),
            "log_level": buf.current_level() if buf else "default",
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}/logs",
        vol.Optional("since", default=0): int,
        vol.Optional("level", default="all"): vol.In(list(LEVELS)),
        vol.Optional("limit", default=500): vol.All(int, vol.Range(min=1, max=2000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_logs(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    if buf is None:
        connection.send_error(msg["id"], "not_ready", "Protokoll-Aufzeichnung nicht aktiv")
        return
    connection.send_result(msg["id"], {"entries": buf.entries(msg["since"], LEVELS[msg["level"]], msg["limit"])})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}/log_level", vol.Required("level"): vol.In(["default", "info", "debug"])}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_log_level(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    connection.send_result(msg["id"], {"level": buf.set_level(msg["level"]) if buf else "default"})


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/clear_logs"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_clear(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    if buf:
        buf.clear()
        buf.add_marker("Protokoll geleert")
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/diagnostics"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_diagnostics(hass: HomeAssistant, connection, msg) -> None:
    """Diagnose von EHS Sentinel anfordern (Task-Übersicht, Warteschlange, Paketzähler → Protokoll)."""
    if hass.services.has_service(EHS_DOMAIN, "request_diagnostic_logs"):
        await hass.services.async_call(EHS_DOMAIN, "request_diagnostic_logs", {}, blocking=True)
        connection.send_result(msg["id"], {"ok": True})
    else:
        connection.send_error(msg["id"], "not_found", "Dienst ehs_sentinel.request_diagnostic_logs nicht vorhanden")


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/reconnect", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_reconnect(hass: HomeAssistant, connection, msg) -> None:
    """EHS-Sentinel-Integration neu laden → Verbindung neu aufbauen."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != EHS_DOMAIN:
        connection.send_error(msg["id"], "not_found", "EHS-Sentinel-Eintrag nicht gefunden")
        return
    buf = _buffer(hass)
    if buf:
        buf.add_marker(f"Neu verbinden: {entry.title}")
    ok = await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"], {"ok": bool(ok)})


def _packet_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("www", EHS_DOMAIN, "logs"))


def _read_packets(path: Path, lines: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return list(deque(fh, maxlen=lines))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}/packet_log",
        vol.Optional("file"): str,
        vol.Optional("lines", default=200): vol.All(int, vol.Range(min=10, max=2000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_packet_log(hass: HomeAssistant, connection, msg) -> None:
    """Paket-Log von EHS Sentinel (www/ehs_sentinel/logs/packet_*.log) – nur bei aktivem erweiterten Logging."""
    folder = _packet_dir(hass)

    def _list() -> list[str]:
        if not folder.is_dir():
            return []
        return sorted((p.name for p in folder.glob("packet_*.log")), reverse=True)

    files = await hass.async_add_executor_job(_list)
    name = msg.get("file") or (files[0] if files else None)
    lines: list[str] = []
    if name and name in files:
        lines = await hass.async_add_executor_job(_read_packets, folder / name, msg["lines"])
    connection.send_result(msg["id"], {"files": files, "file": name, "lines": [l.rstrip("\n") for l in lines]})


# ---------------- Schnittstelle (Optionen von EHS Sentinel) ----------------
CFG_KEYS = ("ip", "port", "write_mode", "polling", "polling_yaml", "extended_logging", "force_refresh", "diagnostic_logs")
CFG_ERRORS = {
    "connection_failed": "Keine Verbindung zum RS485-Adapter unter dieser Adresse.",
    "already_configured": "Diese IP/Port-Kombination wird schon von einer anderen EHS-Sentinel-Instanz genutzt.",
    "invalid_yaml": "Die Polling-Konfiguration ist kein gültiges YAML.",
}


def _ehs_entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry if entry is not None and entry.domain == EHS_DOMAIN else None


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/config_get", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_config_get(hass: HomeAssistant, connection, msg) -> None:
    entry = _ehs_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "EHS-Sentinel-Eintrag nicht gefunden")
        return
    vals = {k: entry.options.get(k, entry.data.get(k)) for k in CFG_KEYS}
    vals["name"] = entry.title
    connection.send_result(msg["id"], vals)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}/config_set", vol.Required("entry_id"): str, vol.Required("values"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_config_set(hass: HomeAssistant, connection, msg) -> None:
    """Optionen über den offiziellen Options-Dialog von EHS Sentinel setzen (inkl. Prüfung + Neuladen)."""
    entry = _ehs_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "EHS-Sentinel-Eintrag nicht gefunden")
        return
    cur = {k: entry.options.get(k, entry.data.get(k)) for k in CFG_KEYS}
    new = {**cur, **{k: v for k, v in msg["values"].items() if k in CFG_KEYS}}
    try:
        new["port"] = int(new["port"])
    except (TypeError, ValueError):
        connection.send_result(msg["id"], {"ok": False, "error": "Port muss eine Zahl sein."})
        return
    for k in ("write_mode", "polling", "extended_logging", "force_refresh", "diagnostic_logs"):
        new[k] = bool(new.get(k))
    new["polling_yaml"] = new.get("polling_yaml") or ""
    buf = _buffer(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=new)
    rtype = getattr(res.get("type"), "value", res.get("type"))
    if rtype == "create_entry":
        if buf:
            buf.add_marker(f"Schnittstelle gespeichert: {entry.title} → {new['ip']}:{new['port']}")
        connection.send_result(msg["id"], {"ok": True})
        return
    errors = res.get("errors") or {}
    if rtype == "form":
        hass.config_entries.options.async_abort(flow["flow_id"])
    text = "; ".join(CFG_ERRORS.get(v, v) for v in errors.values()) or f"Nicht gespeichert ({rtype})"
    connection.send_result(msg["id"], {"ok": False, "error": text})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}/test_connection", vol.Required("ip"): str, vol.Required("port"): int}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_test_connection(hass: HomeAssistant, connection, msg) -> None:
    """TCP-Verbindung zum RS485-Adapter testen (wie im Options-Dialog von EHS Sentinel)."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(msg["ip"], msg["port"]), timeout=5)
        writer.close()
        await writer.wait_closed()
        connection.send_result(msg["id"], {"ok": True, "ms": round((loop.time() - t0) * 1000)})
    except Exception as err:  # pylint: disable=broad-except
        connection.send_result(msg["id"], {"ok": False, "error": str(err) or err.__class__.__name__})


def async_register(hass: HomeAssistant) -> None:
    for cmd in (ws_status, ws_logs, ws_log_level, ws_clear, ws_diagnostics, ws_reconnect, ws_packet_log,
                ws_config_get, ws_config_set, ws_test_connection):
        websocket_api.async_register_command(hass, cmd)
