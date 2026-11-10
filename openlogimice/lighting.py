"""LED zones (features 0x8070 / 0x8071) and the 11-byte lighting records stored in
on-board profiles.

A record is [effect id, 10 parameter bytes]. Parameter offsets below are relative
to the byte after the effect id; multi-byte values are big-endian.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import hidpp
from .hidpp import HidppDevice, HidppError

F_COLOR_LED_EFFECTS = 0x8070
F_RGB_EFFECTS = 0x8071
RECORD_SIZE = 11

ZONE_LOCATIONS = {0: "LED", 1: "Primary", 2: "Logo", 3: "Left side", 4: "Right side",
                  5: "Combined"}


@dataclass(frozen=True)
class Param:
    name: str      # color, color2, period, intensity, saturation, speed
    offset: int
    size: int


@dataclass(frozen=True)
class Effect:
    id: int
    name: str
    params: tuple[Param, ...] = ()
    defaults: dict = field(default_factory=dict)
    note: str = ""


EFFECTS = {e.id: e for e in (
    Effect(0x00, "Off"),
    Effect(0x01, "Static", (Param("color", 0, 3),)),
    Effect(0x02, "Pulse", (Param("color", 0, 3), Param("speed", 3, 1)), {"speed": 50}),
    Effect(0x03, "Colour cycle", (Param("period", 5, 2), Param("intensity", 7, 1)),
           {"period": 5000, "intensity": 100}),
    Effect(0x0A, "Breathing", (Param("color", 0, 3), Param("period", 3, 2), Param("intensity", 6, 1)),
           {"period": 3000, "intensity": 100}),
    Effect(0x0B, "Ripple", (Param("color", 0, 3), Param("period", 4, 2)), {"period": 20}),
    Effect(0x15, "Colour cycle", (Param("saturation", 1, 1), Param("period", 6, 2), Param("intensity", 8, 1)),
           {"saturation": 255, "period": 5000, "intensity": 100}),
    # 0x18 / 0x19 are not publicly documented; layouts read from the G305 X factory profile.
    Effect(0x18, "Dual colour", (Param("color", 0, 3), Param("color2", 3, 3), Param("period", 6, 2),
                                 Param("intensity", 8, 1)),
           {"color2": 0xFEABFD, "period": 7000, "intensity": 100},
           note="Blends between two colours (factory default effect)."),
    Effect(0x19, "Glow", (Param("color", 0, 3), Param("period", 6, 2), Param("intensity", 8, 1)),
           {"period": 4000, "intensity": 100},
           note="Single-colour glow (factory idle effect)."),
)}


@dataclass
class LightSetting:
    effect: int
    values: dict          # param name -> int
    raw: bytes            # original record, kept for unknown effects/bytes

    @property
    def known(self) -> bool:
        return self.effect in EFFECTS

    @property
    def name(self) -> str:
        e = EFFECTS.get(self.effect)
        return e.name if e else f"Effect 0x{self.effect:02X}"

    def color(self) -> int | None:
        return self.values.get("color")


def decode(record: bytes) -> LightSetting:
    record = bytes(record).ljust(RECORD_SIZE, b"\0")[:RECORD_SIZE]
    eid = record[0] if record[0] != 0xFF else 0x00
    vals = {}
    e = EFFECTS.get(eid)
    if e:
        for p in e.params:
            vals[p.name] = int.from_bytes(record[1 + p.offset:1 + p.offset + p.size], "big")
    return LightSetting(eid, vals, record)


def encode(s: LightSetting) -> bytes:
    e = EFFECTS.get(s.effect)
    if e is None:
        return s.raw
    # keep the original parameter bytes only if the effect is unchanged, otherwise start clean
    out = bytearray(s.raw if s.raw[0] == s.effect else bytes(RECORD_SIZE))
    out[0] = s.effect
    for p in e.params:
        v = s.values.get(p.name, e.defaults.get(p.name, 0xFFFFFF if p.name == "color" else 0))
        out[1 + p.offset:1 + p.offset + p.size] = int(v).to_bytes(p.size, "big")
    return bytes(out)


def with_effect(s: LightSetting, eid: int) -> LightSetting:
    """Switch effect, carrying over the colour and filling defaults."""
    e = EFFECTS[eid]
    vals = dict(e.defaults)
    for p in e.params:
        if p.name in s.values:
            vals[p.name] = s.values[p.name]
    if any(p.name == "color" for p in e.params) and "color" not in vals:
        vals["color"] = 0x00B4FF
    return LightSetting(eid, vals, bytes(RECORD_SIZE))


@dataclass
class Zone:
    index: int
    location: str
    effects: list[int]     # effect ids the firmware supports in this zone


def read_zones(dev: HidppDevice) -> list[Zone]:
    """Discover LED zones and their supported effects. Empty if the mouse has none."""
    try:
        if dev.has(F_RGB_EFFECTS):
            info = dev.request(F_RGB_EFFECTS, 0, 0xFF, 0xFF, 0x00)
            zones = []
            for z in range(info[2]):
                zi = dev.request(F_RGB_EFFECTS, 0, z, 0xFF, 0x00)
                loc, count = (zi[2] << 8) | zi[3], zi[4]
                effects = []
                for e in range(count):
                    r = dev.request(F_RGB_EFFECTS, 0, z, e, 0x00)
                    effects.append((r[2] << 8) | r[3])
                zones.append(Zone(z, ZONE_LOCATIONS.get(loc, "LED"), effects))
            return zones
        if dev.has(F_COLOR_LED_EFFECTS):
            info = dev.request(F_COLOR_LED_EFFECTS, 0)
            zones = []
            for z in range(info[0]):
                zi = dev.request(F_COLOR_LED_EFFECTS, 1, z, 0xFF, 0x00)
                loc, count = (zi[1] << 8) | zi[2], zi[3]
                effects = []
                for e in range(count):
                    r = dev.request(F_COLOR_LED_EFFECTS, 2, z, e, 0x00)
                    effects.append((r[2] << 8) | r[3])
                zones.append(Zone(z, ZONE_LOCATIONS.get(loc, "LED"), effects))
            return zones
    except (HidppError, hidpp.HidppTimeout):
        pass
    return []
