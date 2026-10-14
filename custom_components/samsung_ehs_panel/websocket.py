"""WebSocket-Befehle des Panels: Verbindungsstatus und Protokolle von EHS Sentinel."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

try:
    from homeassistant.const import EVENT_STATE_CHANGED, EVENT_STATE_REPORTED
except ImportError:  # sehr alte HA-Versionen
    from homeassistant.const import EVENT_STATE_CHANGED
    EVENT_STATE_REPORTED = None

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


# ---------------- Zuordnung des Panels in Home Assistant speichern (wie EMS-ESP Manager) ----------------
def _store(hass: HomeAssistant) -> Store:
    data = hass.data.setdefault(DOMAIN, {})
    if "store" not in data:
        data["store"] = Store(hass, 1, f"{DOMAIN}.panel")
    return data["store"]


async def _store_load(hass: HomeAssistant) -> dict:
    data = hass.data.setdefault(DOMAIN, {})
    if "store_data" not in data:
        data["store_data"] = (await _store(hass).async_load()) or {"map": {}, "hc_names": {}}
    return data["store_data"]


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/panel/get"})
@websocket_api.async_response
async def ws_panel_get(hass: HomeAssistant, connection, msg) -> None:
    d = await _store_load(hass)
    connection.send_result(
        msg["id"], {"map": d.get("map", {}), "hc_names": d.get("hc_names", {}), "is_admin": connection.user.is_admin}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}/panel/save", vol.Optional("map"): dict, vol.Optional("hc_names"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_panel_save(hass: HomeAssistant, connection, msg) -> None:
    d = await _store_load(hass)
    if "map" in msg:
        d["map"] = {str(k): str(v) for k, v in msg["map"].items() if v}
    if "hc_names" in msg:
        d["hc_names"] = {str(k): str(v) for k, v in msg["hc_names"].items() if v}
    await _store(hass).async_save(d)
    connection.send_result(msg["id"], {"ok": True})


# ---------------- Werte prüfen: alle Werte direkt bei der Wärmepumpe anfragen ----------------
def _norm(name: str) -> str:
    for p in ("ENUM_", "LVAR_", "NASA_", "VAR_"):
        if name.startswith(p):
            name = name[len(p):]
            break
    parts = name.split("_")
    return (parts[0].lower() + "".join(x.capitalize() for x in parts[1:])).lower()


def _load_nasa_keys(path: str) -> dict:
    import yaml  # in HA immer vorhanden

    with open(path, "r", encoding="utf-8") as fh:
        repo = yaml.safe_load(fh) or {}
    return {_norm(k): k for k, v in repo.items() if isinstance(v, dict) and "address" in v}


async def _nasa_map(hass: HomeAssistant) -> dict:
    data = hass.data.setdefault(DOMAIN, {})
    if "nasa_map" not in data:
        path = hass.config.path("custom_components", EHS_DOMAIN, "data", "nasa_repository.yml")
        try:
            data["nasa_map"] = await hass.async_add_executor_job(_load_nasa_keys, path)
        except Exception:  # pylint: disable=broad-except
            data["nasa_map"] = {}
    return data["nasa_map"]


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}/verify",
        vol.Required("entity_ids"): [str],
        vol.Optional("entry_id"): str,
        vol.Optional("timeout", default=20): vol.All(int, vol.Range(min=5, max=90)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_verify(hass: HomeAssistant, connection, msg) -> None:
    """Fragt die Werte bei der WP an (nur lesen) und meldet, welche Entitäten eine Antwort erhalten."""
    coords = hass.data.get(EHS_DOMAIN, {}) or {}
    ent_reg = er.async_get(hass)
    nasa = await _nasa_map(hass)
    if not nasa:
        connection.send_error(msg["id"], "no_repo", "NASA-Liste von EHS Sentinel nicht gefunden")
        return

    by_entry: dict[str, dict[str, str]] = {}
    no_key: list[str] = []
    for eid in msg["entity_ids"]:
        reg = ent_reg.async_get(eid)
        if reg is None or reg.platform != EHS_DOMAIN:
            no_key.append(eid)
            continue
        uid = reg.unique_id or ""
        entry_id = reg.config_entry_id or ""
        if entry_id and uid.startswith(entry_id + "_"):
            k = uid[len(entry_id) + 1:]
        elif uid.startswith("samsung_ehssentinel"):
            k = uid[len("samsung_ehssentinel"):]
        else:
            k = uid.split("_", 1)[-1]
        key = nasa.get(k.lower())
        if not key:
            st_obj = hass.states.get(eid)
            key = st_obj.attributes.get("nasa_name") if st_obj else None
        if not key or entry_id not in coords:
            no_key.append(eid)
            continue
        by_entry.setdefault(entry_id, {})[eid] = key

    targets = {eid for m in by_entry.values() for eid in m}
    answered: set[str] = set()
    done = asyncio.Event()

    @callback
    def _flt(event_data) -> bool:
        return event_data.get("entity_id") in targets

    @callback
    def _got(event) -> None:
        answered.add(event.data.get("entity_id"))
        if answered >= targets:
            done.set()

    unsubs = [hass.bus.async_listen(EVENT_STATE_CHANGED, _got, event_filter=_flt)]
    if EVENT_STATE_REPORTED:
        unsubs.append(hass.bus.async_listen(EVENT_STATE_REPORTED, _got, event_filter=_flt))
    buf = _buffer(hass)
    if buf:
        buf.add_marker(f"Werte prüfen: {len(targets)} Werte werden angefragt")
    errors = []
    try:
        for entry_id, m in by_entry.items():
            keys = sorted(set(m.values()))
            ok = await coords[entry_id].producer.read_request(list_of_messages=keys, retry_mode=False)
            if ok is False:
                errors.append("Adressen von Innen-/Außengerät noch nicht erkannt – bitte kurz warten.")
        if targets:
            try:
                await asyncio.wait_for(done.wait(), timeout=msg["timeout"])
            except asyncio.TimeoutError:
                pass
    finally:
        for u in unsubs:
            u()
    connection.send_result(
        msg["id"],
        {
            "requested": len(targets),
            "answered": sorted(answered & targets),
            "missing": sorted(targets - answered),
            "no_key": sorted(no_key),
            "errors": errors,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/verify_write", vol.Required("entity_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_verify_write(hass: HomeAssistant, connection, msg) -> None:
    """Schreibt den AKTUELLEN Wert erneut (nichts wird verändert) und wartet auf die Bestätigung der WP."""
    eid = msg["entity_id"]
    state = hass.states.get(eid)
    dom = eid.split(".")[0]
    if state is None or state.state in ("unavailable", "unknown"):
        connection.send_result(msg["id"], {"ok": False, "error": "Entität hat keinen aktuellen Wert"})
        return
    if dom not in ("number", "select", "switch"):
        connection.send_result(msg["id"], {"ok": None, "error": "Nur lesbar (kein Einstellwert)"})
        return
    nasa_name = state.attributes.get("nasa_name")
    if not nasa_name:
        reg = er.async_get(hass).async_get(eid)
        nasa = await _nasa_map(hass)
        if reg is not None:
            uid = reg.unique_id or ""
            k = uid[len(reg.config_entry_id) + 1:] if reg.config_entry_id and uid.startswith(reg.config_entry_id + "_") else uid.replace("samsung_ehssentinel", "", 1)
            nasa_name = nasa.get(k.lower())
    buf = _buffer(hass)
    if buf is None or not nasa_name:
        connection.send_result(msg["id"], {"ok": None, "error": "NASA-Name der Entität unbekannt"})
        return
    lvl = buf.current_level()
    if lvl == "default":
        buf.set_level("info")  # Bestätigungsmeldungen von EHS Sentinel mitlesen
    mark = buf.entries(0, 0, 1)[-1]["id"] if buf.entries(0, 0, 1) else 0
    value = state.state
    if dom == "number":
        service, data = "set_value", {"entity_id": eid, "value": float(value)}
    elif dom == "select":
        service, data = "select_option", {"entity_id": eid, "option": value}
    else:
        service, data = ("turn_on" if value == "on" else "turn_off"), {"entity_id": eid}
    ok, text = None, "Keine Rückmeldung von EHS Sentinel"
    try:
        await asyncio.wait_for(hass.services.async_call(dom, service, data, blocking=True), timeout=25)
        for e in buf.entries(mark, 0, 2000):
            m = e["msg"]
            if nasa_name not in m and "Cannot send write" not in m:
                continue
            if "Confirming write" in m:
                ok, text = True, "Schreiben von der Wärmepumpe bestätigt"
                break
            if "Write failed" in m or "Cannot send write" in m:
                ok, text = False, m
                break
            if "No confirmation" in m:
                ok, text = False, "Keine Bestätigung der Wärmepumpe"
    except asyncio.TimeoutError:
        ok, text = False, "Zeitüberschreitung beim Schreiben"
    except Exception as err:  # pylint: disable=broad-except
        ok, text = False, str(err)
    finally:
        if lvl == "default":
            buf.set_level("default")
    connection.send_result(msg["id"], {"ok": ok, "error": None if ok else text, "text": text, "value": value, "nasa_name": nasa_name})


def async_register(hass: HomeAssistant) -> None:
    for cmd in (ws_status, ws_logs, ws_log_level, ws_clear, ws_diagnostics, ws_reconnect, ws_packet_log,
                ws_config_get, ws_config_set, ws_test_connection, ws_panel_get, ws_panel_save, ws_verify, ws_verify_write):
        websocket_api.async_register_command(hass, cmd)
