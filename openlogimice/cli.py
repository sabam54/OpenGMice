"""Command line access: `python -m openlogimice info|dpi N|backup FILE|restore FILE`."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import actions, hidpp
from .mouse import Mouse, discover_mice
from .profiles import crc_ok


RULE_FILE = "42-openlogimice.rules"


def udev_rule() -> str:
    """The rule shipped next to the package (repo root, or /app/share/openlogimice in the Flatpak)."""
    return (Path(__file__).resolve().parent.parent / RULE_FILE).read_text()


def udev_install_command() -> str:
    run = f"flatpak run {os.environ['FLATPAK_ID']}" if os.environ.get("FLATPAK_ID") else "python3 -m openlogimice"
    return (f"{run} udev-rule | sudo tee /etc/udev/rules.d/{RULE_FILE} && "
            "sudo udevadm control --reload && sudo udevadm trigger")


def _open(match: str | None) -> Mouse:
    """Open the first mouse that answers, or the first whose name / product id contains `match`."""
    nodes = hidpp.find_logitech_hidraw()
    if not nodes:
        raise SystemExit("no Logitech HID++ device found")
    if not any(os.access(n["path"], os.R_OK | os.W_OK) for n in nodes):
        raise SystemExit("no permission to open the mouse. Install the udev rule, then replug it:\n  "
                         + udev_install_command())
    errors = []
    for n in nodes:
        try:
            m = Mouse(n)
        except Exception as e:  # noqa: BLE001 - try the next endpoint
            errors.append(f"{n['name']}: {e}")
            continue
        key = f"{m.name} {n['name']} {m.pid:04x}".lower()
        if match is None or match.lower() in key:
            return m
        m.close()
    raise SystemExit("no matching mouse" + ("\n  " + "\n  ".join(errors) if errors else ""))


def main(argv):
    ap = argparse.ArgumentParser(prog="openlogimice")
    ap.add_argument("-d", "--device", help="pick a mouse by part of its name or product id, e.g. 'G305 X' or 4074")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list connected mice")
    sub.add_parser("udev-rule", help="print the udev rule that gives your user access to the mouse")
    sub.add_parser("info", help="show device, battery and profiles")
    d = sub.add_parser("dpi", help="set live DPI (not saved)")
    d.add_argument("x", type=int)
    d.add_argument("y", type=int, nargs="?")
    sub.add_parser("onboard", help="switch back to on-board memory mode")
    b = sub.add_parser("backup", help="save all on-board memory sectors to a JSON file")
    b.add_argument("file")
    r = sub.add_parser("restore", help="write a backup made with 'backup' back to the mouse")
    r.add_argument("file")
    a = ap.parse_args(argv)

    if a.cmd == "udev-rule":
        print(udev_rule(), end="")
        return 0
    if a.cmd == "list":
        for d in discover_mice():      # one line per mouse, even if reachable two ways
            n = d["nodes"][0]
            print(f"{d['name']}  [{d['link']}, 046D:{n['pid']:04X}, {n['path']}]")
        return 0
    m = _open(a.device)
    ob = m.onboard
    if a.cmd == "info":
        bat = m.battery()
        print(f"{m.name}  [{m.bus}, 046D:{m.pid:04X}, fw {m.firmware}]")
        print(f"battery: {bat.percent}% {bat.status}" if bat else "battery: n/a")
        print(f"live DPI: {m.get_dpi()}  sensor range {m.dpi_min}-{m.dpi_max}")
        if ob:
            print(f"memory mode: {'onboard' if ob.get_mode() == 1 else 'host'}, "
                  f"active sector 0x{ob.get_current_sector():04X}, format {ob.info.fmt}")
            profiles, rom = ob.load_all()
            for p in [rom] + profiles:
                tag = "ROM default" if p.is_rom else f"slot {p.sector}{'' if p.enabled else ' (unused)'}"
                print(f"\n  {p.name or 'Default'}  — {tag}")
                print(f"    report rate {p.report_rate} Hz, DPI " +
                      ", ".join(f"{s.x}{'' if s.x == s.y else '/' + str(s.y)}{'*' if i == p.default_dpi else ''}"
                                for i, s in enumerate(p.stages)))
                print("    buttons: " + " | ".join(f"G{i + 1}={actions.describe(x)}" for i, x in enumerate(p.buttons)))
    elif a.cmd == "dpi":
        m.preview_dpi(m.snap_dpi(a.x), m.snap_dpi(a.y or a.x))
        print("live DPI now", m.get_dpi(), "(host mode; run 'onboard' or replug to return)")
    elif a.cmd == "onboard":
        m.onboard.set_mode(1)
        print("back to on-board memory mode, DPI", m.get_dpi())
    elif a.cmd == "backup":
        sectors = {}
        for s in range(0, ob.info.profile_count + 1):
            try:
                sectors[str(s)] = ob.read_sector(s).hex()
            except hidpp.HidppError:   # never-written sector on some mice
                pass
        data = {"device": m.name, "pid": m.pid, "format": ob.info.fmt, "sectors": sectors}
        with open(a.file, "w") as f:
            json.dump(data, f, indent=1)
        print(f"saved {len(data['sectors'])} sectors to {a.file}")
    elif a.cmd == "restore":
        with open(a.file) as f:
            data = json.load(f)
        if data["pid"] != m.pid or data["format"] != ob.info.fmt:
            raise SystemExit("backup is from a different device")
        for s, h in data["sectors"].items():
            raw = bytes.fromhex(h)
            if crc_ok(raw):
                ob.write_sector(int(s), raw)
                print(f"restored sector {s}")
            else:
                print(f"skipped sector {s} (empty/invalid in backup)")
    m.close()
    return 0
