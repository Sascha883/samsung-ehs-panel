"""Filter gegen Log-Flut von EHS Sentinel bei nicht vorhandenen Bauteilen.

EHS Sentinel (2.0.x) wertet ENUM-Werte per Tabellen-Lookup aus. Meldet das Außengerät für ein
Bauteil 0xFF (= „nicht vorhanden"), fehlt dieser Wert in der Tabelle und EHS Sentinel schreibt
bei jedem Paket zwei ERROR-Zeilen inkl. kompletter Paketausgabe ins Log – alle ~11 s.
Dieser Filter blendet genau diese Meldungen aus und protokolliert jeden betroffenen Wert einmalig.
"""
from __future__ import annotations

import logging
import re

LOGGER_NAMES = ("custom_components.ehs_sentinel.coordinator", "custom_components.ehs_sentinel")
_ERR_RE = re.compile(r"Error determining value for message (\S+) with payload b'((?:\\xff)+)'$")
_LOG = logging.getLogger(__name__)


class FfPayloadFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.skip_next_details = False
        self.counts: dict[str, int] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        msg = msg.split("] ", 1)[1] if msg.startswith("[") and "] " in msg else msg
        m = _ERR_RE.search(msg)
        if m:
            name = m.group(1)
            first = name not in self.counts
            self.counts[name] = self.counts.get(name, 0) + 1
            self.skip_next_details = True
            if first:
                _LOG.info("EHS Sentinel: %s meldet 0xFF (Bauteil nicht vorhanden) – "
                          "weitere Fehlermeldungen dazu werden ausgeblendet", name)
            return False
        if self.skip_next_details and msg.startswith("Packet details:"):
            self.skip_next_details = False
            return False
        self.skip_next_details = False
        return True


def install() -> FfPayloadFilter:
    f = FfPayloadFilter()
    for name in LOGGER_NAMES:
        logging.getLogger(name).addFilter(f)
    return f


def uninstall(f: FfPayloadFilter) -> None:
    for name in LOGGER_NAMES:
        logging.getLogger(name).removeFilter(f)
