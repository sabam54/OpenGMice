"""Minimal HID++ 2.0 transport over Linux hidraw.

Works for Logitech devices connected directly (USB cable / Bluetooth, device
index 0xFF) and behind a LIGHTSPEED/Unifying receiver (device index 1..6).
"""
from __future__ import annotations

import os
import select
import threading
import time

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
LONG_LEN = 20
SW_ID = 0x0B  # our software id (low nibble of byte 3)

LOGITECH_VID = 0x046D

# Feature IDs we care about
F_ROOT = 0x0000
F_FEATURE_SET = 0x0001
F_FW_VERSION = 0x0003
F_DEVICE_NAME = 0x0005
F_BATTERY_STATUS = 0x1000
F_BATTERY_VOLTAGE = 0x1001
F_UNIFIED_BATTERY = 0x1004
F_ADJUSTABLE_DPI = 0x2201
F_EXTENDED_DPI = 0x2202
F_REPORT_RATE = 0x8060
F_EXTENDED_REPORT_RATE = 0x8061
F_ONBOARD_PROFILES = 0x8100

HIDPP_ERRORS = {
    1: "unknown", 2: "invalid argument", 3: "out of range", 4: "hardware error",
    5: "logitech internal", 6: "invalid feature index", 7: "invalid function",
    8: "busy", 9: "unsupported",
}


class HidppError(Exception):
    def __init__(self, code: int, feature: int, function: int):
        self.code = code
        super().__init__(
            f"HID++ error 0x{code:02X} ({HIDPP_ERRORS.get(code, '?')}) "
            f"on feature 0x{feature:04X} fn {function}")


class HidppTimeout(Exception):
    pass


def _read_sysfs(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _has_hidpp_reports(hidraw: str) -> bool:
    """HID++ capable interfaces declare vendor report IDs 0x10/0x11 (or 0x11 only)."""
    desc = b""
    try:
        with open(f"/sys/class/hidraw/{hidraw}/device/report_descriptor", "rb") as f:
            desc = f.read()
    except OSError:
        return False
    # Look for "Report ID 0x11" (0x85 0x11) in a vendor usage page (0x06 0x00/0x43 0xFF).
    return b"\x85\x11" in desc and (b"\x06\x00\xff" in desc or b"\x06\x43\xff" in desc)


def is_receiver_pid(bus: int, pid: int) -> bool:
    """Unifying, LIGHTSPEED and Bolt receivers all live in 046D:C5xx."""
    return bus == 0x03 and 0xC500 <= pid <= 0xC5FF


def find_logitech_hidraw() -> list[dict]:
    """Return candidate HID++ endpoints as dicts {path, name, bus, pid, devnum, link}.

    Directly attached devices (USB cable, Bluetooth) and the per-device nodes the
    kernel's hid-logitech-dj driver creates for receiver-paired devices come first;
    raw receiver slots 1..6 are listed last as a fallback.
    """
    out, receivers = [], []
    base = "/sys/class/hidraw"
    if not os.path.isdir(base):
        return out
    for node in sorted(os.listdir(base)):
        uevent = _read_sysfs(f"{base}/{node}/device/uevent")
        info = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        hid_id = info.get("HID_ID", "")
        try:
            bus, vid, pid = (int(x, 16) for x in hid_id.split(":"))
        except ValueError:
            continue
        if vid != LOGITECH_VID or not _has_hidpp_reports(node):
            continue
        entry = {"path": f"/dev/{node}", "name": info.get("HID_NAME", "Logitech device"),
                 "bus": bus, "pid": pid, "devnum": 0xFF, "strict": False}
        if is_receiver_pid(bus, pid):
            receivers += [dict(entry, devnum=n, strict=True, link="Receiver", name=f"{entry['name']} slot {n}")
                          for n in range(1, 7)]
        else:
            dj_child = info.get("DRIVER") == "logitech-hidpp-device"
            entry["link"] = "Wireless" if dj_child else {0x05: "Bluetooth"}.get(bus, "USB")
            out.append(entry)
    return out + receivers


class HidppDevice:
    """One HID++ 2.0 device reachable through a hidraw node."""

    def __init__(self, path: str, devnum: int = 0xFF, strict: bool = False):
        self.path = path
        self.devnum = devnum
        # A receiver node carries traffic for several devices, so replies must match our
        # slot. Direct and hid-logitech-dj child nodes carry one device whose replies may
        # come back with its receiver slot number instead of 0xFF.
        self.strict = strict
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._lock = threading.RLock()
        self._features: dict[int, int] = {}   # feature id -> index
        self.protocol: tuple[int, int] | None = None

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    # ---------------------------------------------------------------- transport
    def _drain(self):
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return
            try:
                os.read(self.fd, 64)
            except BlockingIOError:
                return

    def request_idx(self, index: int, function: int, params: bytes = b"",
                    timeout: float = 2.0) -> bytes:
        """Send a long HID++ 2.0 request and return the 16 parameter bytes of the reply."""
        msg = bytes([REPORT_LONG, self.devnum, index, ((function & 0x0F) << 4) | SW_ID])
        msg = (msg + bytes(params)).ljust(LONG_LEN, b"\0")[:LONG_LEN]
        with self._lock:
            self._drain()
            os.write(self.fd, msg)
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    raise HidppTimeout(f"no reply for feature index {index} fn {function}")
                r, _, _ = select.select([self.fd], [], [], left)
                if not r:
                    continue
                try:
                    d = os.read(self.fd, 64)
                except BlockingIOError:
                    continue
                if len(d) < 7 or d[0] not in (REPORT_SHORT, REPORT_LONG):
                    continue
                if self.strict and d[1] != self.devnum:
                    continue
                if d[2] == 0x8F and d[3] == index and d[4] == msg[3]:  # HID++ 1.0 error (e.g. empty slot)
                    raise HidppError(d[5], index, function)
                if d[2] == 0xFF and d[3] == index and (d[4] & 0x0F) == SW_ID:
                    fid = next((k for k, v in self._features.items() if v == index), index)
                    raise HidppError(d[5], fid, function)
                if d[2] == index and d[3] == msg[3]:
                    return bytes(d[4:]).ljust(16, b"\0")

    def feature_index(self, feature: int) -> int | None:
        if feature == F_ROOT:
            return 0
        if feature not in self._features:
            reply = self.request_idx(0, 0, bytes([feature >> 8, feature & 0xFF]))
            self._features[feature] = reply[0] if reply[0] else 0
        idx = self._features[feature]
        return idx or None

    def has(self, feature: int) -> bool:
        return self.feature_index(feature) is not None

    def request(self, feature: int, function: int, *params, timeout: float = 2.0) -> bytes:
        idx = self.feature_index(feature)
        if idx is None:
            raise HidppError(9, feature, function)
        data = b"".join(p if isinstance(p, (bytes, bytearray)) else bytes([p]) for p in params)
        return self.request_idx(idx, function, data, timeout)

    # ---------------------------------------------------------------- basics
    def ping(self) -> tuple[int, int]:
        r = self.request_idx(0, 1, b"\0\0\x5A")
        self.protocol = (r[0], r[1])
        return self.protocol

    def enumerate_features(self) -> dict[int, int]:
        fs = self.feature_index(F_FEATURE_SET)
        n = self.request_idx(fs, 0)[0]
        for i in range(1, n + 1):
            r = self.request_idx(fs, 1, bytes([i]))
            self._features[(r[0] << 8) | r[1]] = i
        return dict(self._features)

    def name(self) -> str:
        n = self.request(F_DEVICE_NAME, 0)[0]
        out = b""
        while len(out) < n:
            out += self.request(F_DEVICE_NAME, 1, len(out))[: n - len(out)]
        return out.decode("utf-8", "replace").rstrip("\0")

    def unit_id(self) -> str:
        """Per-device serial (feature 0x0003). Tells two endpoints of one mouse apart from two mice."""
        if not self.has(F_FW_VERSION):
            return ""
        return self.request(F_FW_VERSION, 0)[1:5].hex()

    def model_ids(self) -> set[int]:
        """Product IDs of the device on each transport (USB, LIGHTSPEED, Bluetooth...)."""
        if not self.has(F_FW_VERSION):
            return set()
        r = self.request(F_FW_VERSION, 0)
        return {int.from_bytes(r[i:i + 2], "big") for i in (7, 9, 11)} - {0}

    def firmware(self) -> str:
        if not self.has(F_FW_VERSION):
            return ""
        count = self.request(F_FW_VERSION, 0)[0]
        for i in range(count):
            r = self.request(F_FW_VERSION, 1, i)
            if r[0] & 0x0F == 0:  # main application
                prefix = r[1:4].decode("ascii", "replace").strip("\0")
                return f"{prefix} {r[4]:02X}.{r[5]:02X}.{(r[6] << 8) | r[7]:04X}"
        return ""
