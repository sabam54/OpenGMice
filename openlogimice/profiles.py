"""HID++ 2.0 feature 0x8100 (Onboard Profiles): read, edit and write profiles.

Two sector layouts exist:
  * v1 (profile formats 1-5): G402/G303/G900/G502...; matches libratbag/Solaar.
  * v6 (profile formats 6-8): G305 X, PRO X Superlight 2, ... Report rate is an
    index into REPORT_RATES, DPI stages are 5-byte (X le16, Y le16, LOD).

Only fields we understand are changed; every other byte of a sector is carried
through untouched so unknown/vendor data (lighting, write counters, ...) survives.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .hidpp import F_ONBOARD_PROFILES, HidppDevice, HidppError

ROM_BASE = 0x0100
MODE_ONBOARD = 1
MODE_HOST = 2
REPORT_RATES = [125, 250, 500, 1000, 2000, 4000, 8000]  # v6 index table
DPI_SLOTS = 5
BUTTON_SLOTS = 16
LIGHTING = 0xD0          # 4 x 11-byte records: zone 0/1 primary, zone 0/1 passive (v1 and v6)
LIGHTING_RECORDS = 4


def crc_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def with_crc(data: bytes | bytearray) -> bytes:
    data = bytearray(data)
    crc = crc_ccitt(data[:-2])
    data[-2] = crc >> 8
    data[-1] = crc & 0xFF
    return bytes(data)


def crc_ok(data: bytes) -> bool:
    return crc_ccitt(data[:-2]) == ((data[-2] << 8) | data[-1])


@dataclass
class Layout:
    rate_wireless: int
    rate_wired: int
    dpi: int            # offset of [default_idx, shift_idx, stages...] (v6) or [default, shift, le16 x5] (v1)
    stage_size: int     # 5 (v6) or 2 (v1)
    angle_snap: int
    ps_timeout: int
    po_timeout: int
    buttons: int
    gshift: int
    name: int
    rate_is_index: bool


LAYOUT_V1 = Layout(rate_wireless=0x00, rate_wired=0x00, dpi=0x01, stage_size=2, angle_snap=0x11,
                   ps_timeout=0x1C, po_timeout=0x1E, buttons=0x20, gshift=0x60, name=0xA0,
                   rate_is_index=False)
LAYOUT_V6 = Layout(rate_wireless=0x00, rate_wired=0x01, dpi=0x02, stage_size=5, angle_snap=0x22,
                   ps_timeout=0x2C, po_timeout=0x2E, buttons=0x30, gshift=0x70, name=0xA0,
                   rate_is_index=True)


def layout_for(fmt: int) -> Layout:
    return LAYOUT_V6 if fmt >= 6 else LAYOUT_V1


@dataclass
class DpiStage:
    x: int
    y: int
    lod: int = 0


@dataclass
class Profile:
    """Decoded view of a profile sector. `raw` keeps the original bytes."""
    sector: int
    fmt: int
    raw: bytes
    enabled: bool = True
    report_rate: int = 1000          # Hz
    report_rate_wired: int = 1000    # Hz (same byte as report_rate on v1)
    default_dpi: int = 0
    shift_dpi: int = 0
    stages: list[DpiStage] = field(default_factory=list)
    buttons: list[bytes] = field(default_factory=list)   # 4-byte bindings
    lighting: list[bytes] = field(default_factory=list)  # 11-byte lighting records
    name: str = ""
    ps_timeout: int = 0
    po_timeout: int = 0

    @property
    def is_rom(self) -> bool:
        return self.sector >= ROM_BASE

    # ----------------------------------------------------------- decoding
    @classmethod
    def decode(cls, sector: int, fmt: int, raw: bytes, n_buttons: int, enabled=True) -> "Profile":
        L = layout_for(fmt)
        p = cls(sector=sector, fmt=fmt, raw=bytes(raw), enabled=enabled)
        p.report_rate = cls._rate_from_byte(raw[L.rate_wireless], L)
        p.report_rate_wired = cls._rate_from_byte(raw[L.rate_wired], L)
        p.default_dpi = raw[L.dpi] if raw[L.dpi] != 0xFF else 0
        p.shift_dpi = raw[L.dpi + 1] if raw[L.dpi + 1] != 0xFF else 0
        for i in range(DPI_SLOTS):
            base = L.dpi + 2 + i * L.stage_size
            x = raw[base] | (raw[base + 1] << 8)
            if L.stage_size == 5:
                y = raw[base + 2] | (raw[base + 3] << 8)
                lod = raw[base + 4]
            else:
                y, lod = x, 0
            if x in (0, 0xFFFF):
                break
            p.stages.append(DpiStage(x, y if y not in (0, 0xFFFF) else x, lod))
        p.buttons = [raw[L.buttons + 4 * i: L.buttons + 4 * i + 4] for i in range(n_buttons)]
        p.lighting = [raw[LIGHTING + 11 * i: LIGHTING + 11 * i + 11] for i in range(LIGHTING_RECORDS)]
        name = raw[L.name:L.name + 48]
        if name != b"\xff" * 48:
            p.name = name.decode("utf-16le", "replace").split("\0")[0].strip("￿").strip()
        p.ps_timeout = raw[L.ps_timeout] | (raw[L.ps_timeout + 1] << 8)
        p.po_timeout = raw[L.po_timeout] | (raw[L.po_timeout + 1] << 8)
        return p

    @staticmethod
    def _rate_from_byte(b: int, L: Layout) -> int:
        if L.rate_is_index:
            return REPORT_RATES[b] if b < len(REPORT_RATES) else 1000
        return 1000 // b if 0 < b < 0xFF else 1000

    @staticmethod
    def _rate_to_byte(hz: int, L: Layout) -> int:
        if L.rate_is_index:
            return REPORT_RATES.index(hz)
        return max(1, 1000 // hz)

    # ----------------------------------------------------------- encoding
    def encode(self, size: int) -> bytes:
        L = layout_for(self.fmt)
        d = bytearray(self.raw.ljust(size, b"\xff")[:size])
        if not 1 <= len(self.stages) <= DPI_SLOTS:
            raise ValueError("a profile needs 1 to 5 DPI stages")
        d[L.rate_wireless] = self._rate_to_byte(self.report_rate, L)
        if L.rate_wired != L.rate_wireless:
            d[L.rate_wired] = self._rate_to_byte(self.report_rate_wired, L)
        d[L.dpi] = min(self.default_dpi, len(self.stages) - 1)
        d[L.dpi + 1] = min(self.shift_dpi, len(self.stages) - 1)
        first_lod = d[L.dpi + 6]
        for i in range(DPI_SLOTS):
            base = L.dpi + 2 + i * L.stage_size
            if i >= len(self.stages):
                d[base:base + L.stage_size] = bytes(L.stage_size)
                continue
            s = self.stages[i]
            had_stage = int.from_bytes(self.raw[base:base + 2], "little") not in (0, 0xFFFF)
            d[base:base + 2] = s.x.to_bytes(2, "little")
            if L.stage_size == 5:
                d[base + 2:base + 4] = s.y.to_bytes(2, "little")
                # LOD is never edited: keep the stored byte, new stages copy stage 1's.
                d[base + 4] = self.raw[base + 4] if had_stage else first_lod
        for i, b in enumerate(self.buttons):
            d[L.buttons + 4 * i: L.buttons + 4 * i + 4] = b
        for i, rec in enumerate(self.lighting):
            d[LIGHTING + 11 * i: LIGHTING + 11 * i + 11] = rec
        if self.name:
            d[L.name:L.name + 48] = self.name[:23].ljust(24, "\0").encode("utf-16le")
        d[L.ps_timeout:L.ps_timeout + 2] = self.ps_timeout.to_bytes(2, "little")
        d[L.po_timeout:L.po_timeout + 2] = self.po_timeout.to_bytes(2, "little")
        return with_crc(d)


@dataclass
class OnboardInfo:
    memory_model: int
    fmt: int
    macro_fmt: int
    profile_count: int
    profile_count_oob: int
    button_count: int
    sector_count: int
    sector_size: int
    gshift: bool


class OnboardProfiles:
    def __init__(self, dev: HidppDevice):
        self.dev = dev
        r = dev.request(F_ONBOARD_PROFILES, 0)
        self.info = OnboardInfo(
            memory_model=r[0], fmt=r[1], macro_fmt=r[2], profile_count=r[3],
            profile_count_oob=r[4], button_count=r[5], sector_count=r[6],
            sector_size=(r[7] << 8) | r[8], gshift=(r[9] & 0x03) == 0x02)
        if self.info.memory_model != 1:
            raise RuntimeError(f"unsupported onboard memory model {self.info.memory_model}")

    # ----------------------------------------------------------- raw memory
    def _req(self, fn, *params):
        return self.dev.request(F_ONBOARD_PROFILES, fn, *params)

    def read_sector(self, sector: int) -> bytes:
        size = self.info.sector_size
        out = b""
        o = 0
        while o < size - 15:
            out += self._req(5, sector >> 8, sector & 0xFF, o >> 8, o & 0xFF)
            o += 16
        if o < size:  # last partial chunk must be read ending exactly at the sector end
            last = self._req(5, sector >> 8, sector & 0xFF, (size - 16) >> 8, (size - 16) & 0xFF)
            out += last[16 - (size - o):]
        return out[:size]

    def write_sector(self, sector: int, data: bytes, verify: bool = True):
        size = self.info.sector_size
        if not 0 <= sector < min(ROM_BASE, self.info.sector_count):
            raise ValueError(f"refusing to write non-writable sector 0x{sector:04X}")
        if len(data) != size:
            raise ValueError("sector data has wrong size")
        if not crc_ok(data):
            raise ValueError("sector data has a bad CRC")
        self._req(6, sector >> 8, sector & 0xFF, 0, 0, size >> 8, size & 0xFF)
        for o in range(0, size, 16):
            self._req(7, data[o:o + 16].ljust(16, b"\xff"))
        self._req(8)
        if verify:
            back = self.read_sector(sector)
            if back != data:
                raise IOError(f"verification of sector 0x{sector:04X} failed")

    # ----------------------------------------------------------- mode / current
    def get_mode(self) -> int:
        return self._req(2)[0]

    def set_mode(self, mode: int):
        self._req(1, mode)

    def get_current_sector(self) -> int:
        r = self._req(4)
        return (r[0] << 8) | r[1]

    def set_current_sector(self, sector: int):
        self._req(3, sector >> 8, sector & 0xFF)

    def activate(self, sector: int):
        """Make `sector` the active profile and have the firmware re-apply it.

        Re-selecting the active profile may not make the firmware re-apply it, so hop to
        another profile first when the mouse allows it. The hop is best effort: the
        G305 X refuses the ROM profile once user profiles exist, and disabled slots are
        always refused; its firmware re-applies the profile after the write anyway.
        """
        if self.get_current_sector() == sector:
            hops = self.rom_profile_sectors()[:1] + [s for s, en in self.read_directory() if en and s != sector]
            for hop in hops:
                try:
                    self.set_current_sector(hop)
                    break
                except HidppError:
                    continue
        self.set_current_sector(sector)

    def get_current_dpi_index(self) -> int:
        return self._req(0x0B)[0]

    def set_current_dpi_index(self, idx: int):
        self._req(0x0C, idx)

    # ----------------------------------------------------------- directory
    def read_directory(self, rom: bool = False) -> list[tuple[int, bool]]:
        try:
            raw = self.read_sector(ROM_BASE if rom else 0)
        except HidppError:
            # Some mice (original G305) refuse to read a never-written flash sector.
            return []
        entries = []
        for o in range(0, len(raw) - 3, 4):
            sec = (raw[o] << 8) | raw[o + 1]
            if sec in (0xFFFF, 0x0000):
                break
            entries.append((sec, raw[o + 2] == 1))
        return entries

    def write_directory(self, entries: list[tuple[int, bool]]):
        d = bytearray(b"\xff" * self.info.sector_size)
        for i, (sec, en) in enumerate(entries):
            d[i * 4:i * 4 + 4] = bytes([sec >> 8, sec & 0xFF, 1 if en else 0, 0xFF])
        self.write_sector(0, with_crc(d))

    def rom_profile_sectors(self) -> list[int]:
        return [s for s, _ in self.read_directory(rom=True)] or [ROM_BASE + 1]

    # ----------------------------------------------------------- profiles
    def load_profile(self, sector: int, enabled=True) -> Profile:
        raw = self.read_sector(sector)
        return Profile.decode(sector, self.info.fmt, raw, self.info.button_count, enabled)

    def load_all(self) -> tuple[list[Profile], Profile]:
        """Return (user profiles 1..N, ROM default). Empty/invalid flash slots
        are filled from the ROM default with enabled=False so they can be edited."""
        rom_sec = self.rom_profile_sectors()[0]
        rom = self.load_profile(rom_sec)
        directory = dict(self.read_directory())
        profiles = []
        for n in range(1, self.info.profile_count + 1):
            p = None
            if n in directory:
                try:
                    raw = self.read_sector(n)
                except HidppError:
                    raw = b""
                if raw and crc_ok(raw):
                    p = Profile.decode(n, self.info.fmt, raw, self.info.button_count, directory[n])
            if p is None:
                p = Profile.decode(n, self.info.fmt, rom.raw, self.info.button_count, False)
            if not p.name or p.name == "PROFILE_NAME_DEFAULT":   # G HUB's untranslated placeholder
                p.name = f"Profile {n}"
            profiles.append(p)
        return profiles, rom

    def save_profiles(self, profiles: list[Profile], activate: int | None = None):
        """Write every profile sector that changed, then the directory."""
        size = self.info.sector_size
        for p in profiles:
            if p.is_rom:
                continue
            data = p.encode(size)
            try:
                current = self.read_sector(p.sector)
            except HidppError:
                current = b""
            if current != data:
                self.write_sector(p.sector, data)
            p.raw = data
        entries = [(p.sector, p.enabled) for p in profiles if not p.is_rom]
        if not any(en for _, en in entries) and entries:
            entries[0] = (entries[0][0], True)
            profiles[0].enabled = True
        if self.read_directory() != entries:
            self.write_directory(entries)
        if activate is not None:
            self.activate(activate)
