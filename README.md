# OpenGMice

A Linux tool for the mouse settings Logitech G HUB offers on Windows. It talks to the mouse directly over
Logitech's HID++ 2.0 protocol and stores everything in the mouse's **on-board memory**, so the
settings keep working without the app running, on any OS.

## Supported devices
It is not tied to one model: everything is discovered from the mouse at runtime (feature table,
DPI range, button count, profile format, LED zones).

| Status | Devices |
|---|---|
| **Tested** | G305 X SUPERLIGHT (046D:B046, profile format 7) over Bluetooth; G305 (046D:4074, profile format 3) over its LIGHTSPEED receiver |
| **Should work, untested** | Logitech G mice with on-board profiles (HID++ feature 0x8100): G305, G403, G502 family, G603, G703, G705, G900/G903, PRO / PRO X SUPERLIGHT 1-2, G102/G203 ... over USB cable, Bluetooth, or a LIGHTSPEED / Unifying / Bolt receiver |
| **Not supported** | Mice without on-board profiles (MX Master/Anywhere and other office mice), keyboards, headsets, HID++ 1.0 devices, other brands |

Profile formats 1-5 use the layout that libratbag and Solaar use; formats 6-8 use the layout
verified on this G305 X. If you try another mouse, run `python3 -m opengmice backup dump.json`
first and please report the result.

## Features
- **Sensitivity**: up to 5 DPI levels on a draggable bar, default level, DPI-shift level, separate
  X/Y, live preview of the DPI while editing.
- **Report rate**: 125-1000 Hz (wireless and wired values).
- **Assignments**: remap G1-G6 to mouse buttons, key combos (captured from the keyboard),
  shortcuts, media keys, DPI up/down/cycle/shift, profile switching or disable.
- **Lighting**: per-zone effect (off, static, breathing, colour cycle, and on the G305 X
  "dual colour" and "glow"), colours, speed and brightness, for normal and passive state.
  Only shown when the mouse has LEDs.
- **Profiles**: 5 on-board slots: enable, rename, activate, reset to factory default.
- **Device**: battery, firmware and connection info.

## Install (Flatpak)
1. Download `OpenGMice.flatpak` from the [latest release](https://github.com/sabam54/OpenGMice/releases/latest) and install it (it pulls the KDE runtime from Flathub the first time):
   ```bash
   flatpak install --user OpenGMice.flatpak
   ```
2. Let your user open the mouse, once per computer, then replug the mouse or its receiver:
   ```bash
   flatpak run io.github.sabam54.OpenGMice udev-rule | sudo tee /etc/udev/rules.d/42-opengmice.rules && sudo udevadm control --reload && sudo udevadm trigger
   ```
   If Solaar is installed, its rules already cover this and you can skip the step.
3. Start "OpenGMice" from the app menu, or `flatpak run io.github.sabam54.OpenGMice`.
   The command-line tools below work the same way: `flatpak run io.github.sabam54.OpenGMice list`.

To build the bundle yourself: `./packaging/build-flatpak.sh` (needs only `flatpak`; it fetches
`org.flatpak.Builder` and the SDK from Flathub).

## Run from source
```bash
cd ~/OpenGMice && python3 -m opengmice        # GUI (needs PySide6)
python3 -m opengmice list                       # connected mice
python3 -m opengmice info                       # CLI summary (first mouse)
python3 -m opengmice -d 4074 info               # pick a mouse by name or product id
python3 -m opengmice dpi 1600                   # temporary live DPI (switches to host mode)
python3 -m opengmice onboard                    # back to on-board memory mode
python3 -m opengmice backup memory.json         # back up on-board memory
python3 -m opengmice restore memory.json
```
## Permissions
The app needs read/write access to `/dev/hidraw*`. If it says it has no access, install the rule:
```bash
sudo cp 42-opengmice.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
```

## Privacy & security
- No network access: the Flatpak has no network permission and the code makes no connections.
  Nothing is collected or sent anywhere.
- The only thing stored locally is which mouse you used last (Qt settings in your config dir).
- The Flatpak needs `--device=all`, because Flatpak has no narrower permission that covers
  `/dev/hidraw*`. That also exposes other devices inside the sandbox.
- The udev rule gives the user logged in at the screen (`uaccess`) raw access to Logitech HID
  devices, the same as Solaar's rules. That includes Logitech keyboards, so any program running
  as you could read them; leave the rule out if that matters to you on a shared machine.

## Safety
- The factory profile lives in read-only ROM and is never written to. "Reset" copies it back.
- Each written sector carries a CRC and is read back to verify it. A mouse with an invalid sector
  falls back to the ROM profile.
- `python3 -m opengmice backup FILE` saves a mouse's on-board memory; `restore FILE` puts it back.

## Layout notes (format 7)
`0x00` wireless rate index · `0x01` wired rate index · `0x02` default DPI idx · `0x03` shift idx ·
`0x04` 5x[X le16, Y le16, LOD] · `0x2C/0x2E` sleep/off timeouts · `0x30` 16x4-byte buttons ·
`0x70` G-shift buttons · `0xA0` name (UTF-16LE) · `0xD0` 4x11-byte lighting records (zone 0/1
normal, zone 0/1 passive; `[effect, params]`, big-endian) · last 2 bytes CRC-CCITT (BE).

Live DPI changes (feature 0x2202 setDpi) are refused in on-board mode (HID++ error 0x05); the app
switches to host mode while previewing and back to on-board mode afterwards.

## License
GPL-3.0-or-later. OpenGMice is an independent project and is not affiliated with or endorsed
by Logitech. Logitech, G HUB and LIGHTSPEED are trademarks of Logitech.
