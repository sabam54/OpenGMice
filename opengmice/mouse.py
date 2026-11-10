"""High level mouse object built on the HID++ transport."""
from __future__ import annotations

from dataclasses import dataclass

from . import hidpp, lighting
from .hidpp import HidppDevice, HidppError
from .profiles import MODE_HOST, MODE_ONBOARD, OnboardProfiles

BUS_NAMES = {0x03: "USB", 0x05: "Bluetooth"}
# Mice whose LED feature only drives a status/DPI indicator: the firmware ignores the
# lighting stored in profiles (confirmed on a G305), so no Lighting page is offered.
STATUS_LED_ONLY = {0x4074}  # G305 LIGHTSPEED
BATTERY_STATUS = {0: "Discharging", 1: "Charging", 2: "Almost full", 3: "Full",
                  4: "Slow charging", 5: "Invalid battery", 6: "Thermal error"}


@dataclass
class Battery:
    percent: int | None
    status: str
    charging: bool


def _parse_dpi_list(chunks: bytes) -> list[int]:
    """Decode the 0x2201/0x2202 DPI list: values, or 0xE000|step followed by an upper bound."""
    out: list[int] = []
    i = 0
    while i + 1 < len(chunks):
        v = (chunks[i] << 8) | chunks[i + 1]
        if v == 0:
            break
        if v >> 13 == 0b111 and out:
            step = v & 0x1FFF
            last = (chunks[i + 2] << 8) | chunks[i + 3]
            out.extend(range(out[-1] + step, last + 1, step))
            i += 4
        else:
            out.append(v)
            i += 2
    return out


def discover_mice(timeout: float = 1.0) -> list[dict]:
    """One entry per physical mouse with on-board profiles: {name, unit, link, nodes}.

    A receiver-paired mouse is reachable both through its own hidraw node and through
    a receiver slot; both endpoints are kept in `nodes`, best first.
    """
    found: dict[str, dict] = {}
    denied = None
    for n in hidpp.find_logitech_hidraw():
        try:
            d = HidppDevice(n["path"], n.get("devnum", 0xFF), n.get("strict", False))
        except PermissionError as e:
            denied = e
            continue
        except OSError:
            continue
        try:
            d.request_idx(0, 1, b"\0\0\x5A", timeout=timeout)   # ping
            if not d.has(hidpp.F_ONBOARD_PROFILES):
                continue
            unit = d.unit_id() or n["path"]
            if unit in found:
                found[unit]["nodes"].append(n)
                continue
            found[unit] = {"name": d.name() or n["name"], "unit": unit,
                           "link": n.get("link") or BUS_NAMES.get(n["bus"], "USB"), "nodes": [n]}
        except (HidppError, hidpp.HidppTimeout, OSError):
            pass
        finally:
            d.close()
    if not found and denied:
        raise denied    # mice are there but we may not open them: missing udev rule
    return list(found.values())


class Mouse:
    def __init__(self, node: dict):
        self.node = node
        self.dev = HidppDevice(node["path"], node.get("devnum", 0xFF), node.get("strict", False))
        self.dev.ping()
        self.dev.enumerate_features()
        self.name = self.dev.name() or node["name"]
        self.firmware = self.dev.firmware()
        self.unit_id = self.dev.unit_id()
        self.model_ids = self.dev.model_ids() | {node["pid"]}
        self.bus = node.get("link") or BUS_NAMES.get(node["bus"], "USB")
        self.pid = node["pid"]
        self.onboard = OnboardProfiles(self.dev) if self.dev.has(hidpp.F_ONBOARD_PROFILES) else None
        self.dpi_values = self._read_dpi_values()
        self.previewing = False
        self.zones = [] if self.model_ids & STATUS_LED_ONLY else lighting.read_zones(self.dev)

    def close(self):
        self.dev.close()

    # ------------------------------------------------------------ battery
    def battery(self) -> Battery | None:
        d = self.dev
        try:
            if d.has(hidpp.F_UNIFIED_BATTERY):
                r = d.request(hidpp.F_UNIFIED_BATTERY, 1)
                level = {8: 100, 4: 60, 2: 20, 1: 5}.get(r[1])
                pct = r[0] or level
                return Battery(pct, BATTERY_STATUS.get(r[2], "?"), r[2] in (1, 2, 4))
            if d.has(hidpp.F_BATTERY_STATUS):
                r = d.request(hidpp.F_BATTERY_STATUS, 0)
                return Battery(r[0], BATTERY_STATUS.get(r[2], "?"), r[2] in (1, 2, 4))
        except (HidppError, hidpp.HidppTimeout):
            pass
        return None

    # ------------------------------------------------------------ DPI
    def _read_dpi_values(self) -> list[int]:
        d = self.dev
        try:
            if d.has(hidpp.F_EXTENDED_DPI):
                data = b""
                for page in range(32):
                    r = d.request(hidpp.F_EXTENDED_DPI, 2, 0, 0, page)
                    data += r[3:]
                    if data[-2:] == b"\0\0":
                        break
                return _parse_dpi_list(data)
            if d.has(hidpp.F_ADJUSTABLE_DPI):
                data = b""
                for page in range(32):
                    r = d.request(hidpp.F_ADJUSTABLE_DPI, 1, 0, page)
                    data += r[1:]
                    if data[-2:] == b"\0\0":
                        break
                return _parse_dpi_list(data)
        except (HidppError, hidpp.HidppTimeout):
            pass
        return list(range(100, 25601, 50))

    @property
    def dpi_min(self) -> int:
        return self.dpi_values[0]

    @property
    def dpi_max(self) -> int:
        return self.dpi_values[-1]

    def snap_dpi(self, value: int) -> int:
        return min(self.dpi_values, key=lambda v: abs(v - value))

    def get_dpi(self) -> tuple[int, int] | None:
        d = self.dev
        try:
            if d.has(hidpp.F_EXTENDED_DPI):
                r = d.request(hidpp.F_EXTENDED_DPI, 5, 0)
                x = (r[1] << 8) | r[2]
                y = (r[5] << 8) | r[6]
                return x, y or x
            if d.has(hidpp.F_ADJUSTABLE_DPI):
                r = d.request(hidpp.F_ADJUSTABLE_DPI, 2, 0)
                x = (r[1] << 8) | r[2]
                return x, x
        except (HidppError, hidpp.HidppTimeout):
            pass
        return None

    def preview_dpi(self, x: int, y: int | None = None):
        """Temporarily run the sensor at x/y DPI. The firmware refuses host DPI
        changes in on-board mode, so this switches to host mode until end_preview()."""
        if self.onboard and self.onboard.get_mode() != MODE_HOST:
            self.onboard.set_mode(MODE_HOST)
            self.previewing = True
        self.set_dpi(x, y)

    def end_preview(self):
        """Return to on-board mode; the mouse reloads its active profile's DPI."""
        if self.previewing and self.onboard:
            self.onboard.set_mode(MODE_ONBOARD)
        self.previewing = False

    def set_dpi(self, x: int, y: int | None = None):
        """Set the live sensor DPI. Only accepted in host mode on most mice."""
        y = x if y is None else y
        d = self.dev
        if d.has(hidpp.F_EXTENDED_DPI):
            r = d.request(hidpp.F_EXTENDED_DPI, 5, 0)
            lod = r[9]
            d.request(hidpp.F_EXTENDED_DPI, 6, 0, x.to_bytes(2, "big"), y.to_bytes(2, "big"), lod)
        elif d.has(hidpp.F_ADJUSTABLE_DPI):
            d.request(hidpp.F_ADJUSTABLE_DPI, 3, 0, x.to_bytes(2, "big"))

    def max_report_rate(self) -> int:
        """Highest report rate we offer. Formats >= 6 can encode up to 8 kHz but
        only LIGHTSPEED 8K devices accept it, so stay at 1 kHz unless known."""
        return 1000
