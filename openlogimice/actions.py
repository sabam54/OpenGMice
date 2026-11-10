"""Button binding encoding (4-byte onboard-profile button entries) and catalogs."""
from __future__ import annotations

from dataclasses import dataclass

DISABLED = b"\xff\xff\xff\xff"


@dataclass(frozen=True)
class Action:
    category: str
    label: str
    raw: bytes


def mouse_button(mask: int) -> bytes:
    return bytes([0x80, 0x01, mask >> 8, mask & 0xFF])


def key(usage: int, modifiers: int = 0) -> bytes:
    return bytes([0x80, 0x02, modifiers, usage])


def consumer(usage: int) -> bytes:
    return bytes([0x80, 0x03, usage >> 8, usage & 0xFF])


def function(code: int) -> bytes:
    return bytes([0x90, code, 0x00, 0x00])


MOUSE_BUTTONS = {
    0x0001: "Left Click", 0x0002: "Right Click", 0x0004: "Middle Click",
    0x0008: "Back", 0x0010: "Forward",
}

FUNCTIONS = {
    0x01: "Scroll Left", 0x02: "Scroll Right",
    0x03: "DPI Up", 0x04: "DPI Down", 0x05: "DPI Cycle", 0x06: "DPI Default",
    0x07: "DPI Shift", 0x08: "Next Profile", 0x09: "Previous Profile",
    0x0A: "Cycle Profiles", 0x0B: "G-Shift", 0x0C: "Battery Status",
}

CONSUMER = {
    0x00CD: "Play / Pause", 0x00B5: "Next Track", 0x00B6: "Previous Track",
    0x00B7: "Stop", 0x00E2: "Mute", 0x00E9: "Volume Up", 0x00EA: "Volume Down",
    0x0192: "Calculator", 0x018A: "Mail", 0x0223: "Browser Home",
    0x0224: "Browser Back", 0x0225: "Browser Forward", 0x0227: "Browser Refresh",
    0x0221: "Search", 0x006F: "Brightness Up", 0x0070: "Brightness Down",
}

MODIFIERS = [(0x01, "Ctrl"), (0x02, "Shift"), (0x04, "Alt"), (0x08, "Super"),
             (0x10, "RCtrl"), (0x20, "RShift"), (0x40, "AltGr"), (0x80, "RSuper")]

HID_KEY_NAMES: dict[int, str] = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    HID_KEY_NAMES[0x04 + _i] = _c
for _i, _c in enumerate("1234567890"):
    HID_KEY_NAMES[0x1E + _i] = _c
HID_KEY_NAMES.update({
    0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab", 0x2C: "Space",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\", 0x33: ";", 0x34: "'",
    0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/", 0x39: "Caps Lock",
    0x46: "Print Screen", 0x47: "Scroll Lock", 0x48: "Pause", 0x49: "Insert",
    0x4A: "Home", 0x4B: "Page Up", 0x4C: "Delete", 0x4D: "End", 0x4E: "Page Down",
    0x4F: "Right", 0x50: "Left", 0x51: "Down", 0x52: "Up", 0x53: "Num Lock",
    0x54: "KP /", 0x55: "KP *", 0x56: "KP -", 0x57: "KP +", 0x58: "KP Enter",
    0x65: "Menu",
})
for _i in range(12):
    HID_KEY_NAMES[0x3A + _i] = f"F{_i + 1}"
for _i in range(12):
    HID_KEY_NAMES[0x68 + _i] = f"F{_i + 13}"
for _i in range(9):
    HID_KEY_NAMES[0x59 + _i] = f"KP {_i + 1}"
HID_KEY_NAMES[0x62] = "KP 0"


def catalog() -> list[Action]:
    acts = [Action("Mouse", n, mouse_button(m)) for m, n in MOUSE_BUTTONS.items()]
    acts += [Action("Sensitivity & Profiles", n, function(c)) for c, n in FUNCTIONS.items()]
    acts += [Action("Media & System", n, consumer(u)) for u, n in CONSUMER.items()]
    acts += [
        Action("Shortcuts", "Copy (Ctrl+C)", key(0x06, 0x01)),
        Action("Shortcuts", "Paste (Ctrl+V)", key(0x19, 0x01)),
        Action("Shortcuts", "Cut (Ctrl+X)", key(0x1B, 0x01)),
        Action("Shortcuts", "Undo (Ctrl+Z)", key(0x1D, 0x01)),
        Action("Shortcuts", "Redo (Ctrl+Shift+Z)", key(0x1D, 0x03)),
        Action("Shortcuts", "Select All (Ctrl+A)", key(0x04, 0x01)),
        Action("Shortcuts", "Switch Window (Alt+Tab)", key(0x2B, 0x04)),
        Action("Shortcuts", "Close Tab (Ctrl+W)", key(0x1A, 0x01)),
        Action("Shortcuts", "New Tab (Ctrl+T)", key(0x17, 0x01)),
        Action("Shortcuts", "Next Tab (Ctrl+Tab)", key(0x2B, 0x01)),
        Action("Shortcuts", "Previous Tab (Ctrl+Shift+Tab)", key(0x2B, 0x03)),
        Action("Shortcuts", "Show Desktop (Super+D)", key(0x07, 0x08)),
        Action("Shortcuts", "Lock Screen (Super+L)", key(0x0F, 0x08)),
        Action("Shortcuts", "Screenshot (Print Screen)", key(0x46)),
    ]
    acts.append(Action("Other", "Disabled", DISABLED))
    return acts


def describe(raw: bytes) -> str:
    raw = bytes(raw)
    if raw == DISABLED or raw[0] == 0xFF:
        return "Disabled"
    kind = raw[0] >> 4
    if kind == 0x8:
        sub = raw[1]
        if sub == 0x01:
            mask = (raw[2] << 8) | raw[3]
            if mask in MOUSE_BUTTONS:
                return MOUSE_BUTTONS[mask]
            return f"Mouse Button {mask.bit_length()}" if mask & (mask - 1) == 0 else f"Buttons 0x{mask:04X}"
        if sub == 0x02:
            mods = "+".join(n for b, n in MODIFIERS if raw[2] & b)
            k = HID_KEY_NAMES.get(raw[3], f"Key 0x{raw[3]:02X}") if raw[3] else ""
            return "+".join(x for x in (mods, k) if x) or "No key"
        if sub == 0x03:
            u = (raw[2] << 8) | raw[3]
            return CONSUMER.get(u, f"Media 0x{u:04X}")
        if sub == 0x00:
            return "Disabled"
    if kind == 0x9:
        return FUNCTIONS.get(raw[1], f"Function 0x{raw[1]:02X}")
    if kind in (0x0, 0x1):
        return "Macro (onboard)"
    return "Unknown " + raw.hex(" ")


# Qt key -> HID usage. Filled lazily by the GUI (needs Qt imported).
def qt_key_to_hid(qt_key: int) -> int | None:
    from PySide6.QtCore import Qt
    K = Qt.Key
    if K.Key_A.value <= qt_key <= K.Key_Z.value:
        return 0x04 + qt_key - K.Key_A.value
    if K.Key_1.value <= qt_key <= K.Key_9.value:
        return 0x1E + qt_key - K.Key_1.value
    if K.Key_F1.value <= qt_key <= K.Key_F24.value:
        n = qt_key - K.Key_F1.value
        return 0x3A + n if n < 12 else 0x68 + n - 12
    table = {
        K.Key_0: 0x27, K.Key_Return: 0x28, K.Key_Enter: 0x58, K.Key_Escape: 0x29,
        K.Key_Backspace: 0x2A, K.Key_Tab: 0x2B, K.Key_Backtab: 0x2B, K.Key_Space: 0x2C,
        K.Key_Minus: 0x2D, K.Key_Equal: 0x2E, K.Key_BracketLeft: 0x2F,
        K.Key_BracketRight: 0x30, K.Key_Backslash: 0x31, K.Key_Semicolon: 0x33,
        K.Key_Apostrophe: 0x34, K.Key_QuoteLeft: 0x35, K.Key_Comma: 0x36,
        K.Key_Period: 0x37, K.Key_Slash: 0x38, K.Key_CapsLock: 0x39,
        K.Key_Print: 0x46, K.Key_ScrollLock: 0x47, K.Key_Pause: 0x48,
        K.Key_Insert: 0x49, K.Key_Home: 0x4A, K.Key_PageUp: 0x4B, K.Key_Delete: 0x4C,
        K.Key_End: 0x4D, K.Key_PageDown: 0x4E, K.Key_Right: 0x4F, K.Key_Left: 0x50,
        K.Key_Down: 0x51, K.Key_Up: 0x52, K.Key_NumLock: 0x53, K.Key_Menu: 0x65,
    }
    for k, v in table.items():
        if k.value == qt_key:
            return v
    return None
