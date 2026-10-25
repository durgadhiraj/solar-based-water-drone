#!/usr/bin/env python3
"""Generate the wiring schematic set for the Solar Based Water Boat.

Source of truth: CONNECTIONS.md (repo root). The netlist below was extracted from
it by hand; every wire on the schematic sheets is drawn from a netlist entry, and
the script refuses to finish cleanly if a drawn wire does not land on the two pins
its entry names, if two different nets touch, or if any entry is left undrawn.

Usage (from the repo root):
    .venv/Scripts/python tools/make_schematic.py              # PDF + docs/netlist.csv
    .venv/Scripts/python tools/make_schematic.py --png DIR    # also render PNG previews

Outputs:
    docs/boat_circuit_diagram.pdf   A3 landscape, vector, 6 sheets
    docs/boat_circuit_simple.pdf    the whole circuit simplified onto one A3 page
    docs/netlist.csv                one row per connection, with the sheets it is drawn on
"""
from __future__ import annotations

import argparse
import csv
import datetime
import itertools
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import patches  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.textpath import TextPath  # noqa: E402

import schemdraw  # noqa: E402
import schemdraw.elements as elm  # noqa: E402
from schemdraw.segments import Segment, SegmentCircle, SegmentPoly, SegmentText  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_PDF = ROOT / "docs" / "boat_circuit_diagram.pdf"
OUT_CSV = ROOT / "docs" / "netlist.csv"
OUT_SIMPLE = ROOT / "docs" / "boat_circuit_simple.pdf"

PROJECT = "Solar Based Water Boat"
REV = "A"
DATE = datetime.date.today().isoformat()
SOURCE = "CONNECTIONS.md"
N_SHEETS = 6

# ----------------------------------------------------------------------------
# Page geometry and style
# ----------------------------------------------------------------------------
PAGE_W, PAGE_H = 420.0, 297.0            # A3 landscape, mm
INNER = (15.0, 15.0, 405.0, 282.0)        # drawing frame (inside the zone strip)
OUTER = (8.0, 8.0, 412.0, 289.0)
S = 7.0                                   # mm per schematic unit
SW = (INNER[2] - INNER[0]) / S            # schematic area width, units
SH = (INNER[3] - INNER[1]) / S            # schematic area height, units
TB = (225.0, 15.0, 405.0, 61.0)           # title block, mm
TB_U = ((TB[0] - INNER[0]) / S, (TB[3] - INNER[1]) / S)   # title block top-left corner, units


def set_scale(mm_per_unit):
    """Change the schematic scale for the sheets built after this call."""
    global S, SW, SH, TB_U
    S = mm_per_unit
    SW = (INNER[2] - INNER[0]) / S
    SH = (INNER[3] - INNER[1]) / S
    TB_U = ((TB[0] - INNER[0]) / S, (TB[3] - INNER[1]) / S)

FONT = "DejaVu Sans"
FS = 8.0            # minimum / body text size, pt
FS_PIN = 8.0
FONTPROP = FontProperties(family=FONT)
FONTPROP_B = FontProperties(family=FONT, weight="bold")

matplotlib.rcParams.update({"pdf.fonttype": 42, "font.family": FONT, "font.size": FS})
schemdraw.config(fontsize=FS, font=FONT, lw=1.3, color="#1a1a1a")

WIRE = {
    "red": "#d01c1c",
    "black": "#1a1a1a",
    "orange": "#f07d00",
    "yellow": "#f2c200",
    "green": "#12963a",
    "blue": "#1e5bd8",
}
YELLOW_EDGE = "#6e5800"
PART_C = "#1a1a1a"
BODY_FILL = "#f7f7f2"
NOTE_C = "#262626"
WARN_C = "#b00000"
GRID_C = "#4d4d4d"
GAUGE_C = "#4a4a4a"

GAUGE_LW = {
    "14 AWG": 2.8,
    "16–18 AWG": 2.2,
    "18 AWG": 1.9,
    "20–22 AWG": 1.6,
    "22 AWG": 1.4,
    "22–24 AWG": 1.3,
    "component lead": 1.3,
    "—": 1.3,
}

# ----------------------------------------------------------------------------
# Parts (reference designators are shared by every sheet and the BOM)
# ----------------------------------------------------------------------------
PARTS = {
    "PV1": "Solar panel 12 V 10 W polycrystalline",
    "U3": "30 A LCD PWM solar charge controller",
    "BT1": "LiFePO4 pack 12.8 V 15 Ah 4S1P (IFR 32140FS)",
    "F1": "15 A blade fuse + inline holder",
    "SW1": "Main ON/OFF switch ≥ 15 A DC",
    "TB1": "+12 V bus terminal block",
    "TB2": "Ground bus bar (star point)",
    "U2": "UBEC 5 V / 3 A",
    "J1": "2-pin 5 V link (unpluggable)",
    "U1": "ESP32 DevKit (ESP-WROOM-32)",
    **{f"K{n}": "FC65 12 V 30 A optocoupler relay module" for n in (1, 2, 3)},
    "M1": "Rhino IG32 12 V 100 RPM motor (left)",
    "M2": "Rhino IG32 12 V 100 RPM motor (right)",
    "M3": "Conveyor motor (assumed 12 V DC brushed)",
    **{f"D{n}": "Flyback diode 1N5408 / SB560 / SR560 / MBR1045" for n in (1, 2, 3)},
    **{f"C{n}": "0.1 µF ceramic ≥ 50 V (optional)" for n in (1, 2, 3)},
    **{f"R{n}": "10 kΩ relay IN pull-down" for n in (1, 2, 3)},
    **{f"R{n}": "1 kΩ ECHO divider (top)" for n in (4, 5, 6, 7)},
    **{f"R{n}": "2 kΩ ECHO divider (bottom)" for n in (8, 9, 10, 11)},
    **{f"S{n}": "HC-SR04 ultrasonic sensor" for n in (1, 2, 3, 4)},
    **{f"Q{n}": "NPN 2N2222 / BC547 (fallback only)" for n in (1, 2, 3)},
    **{f"R{n}": "1 kΩ base resistor (fallback only)" for n in (12, 13, 14)},
}

# Sheet on which each part's symbol lives (used for off-sheet flag text, verified at the end).
PART_SHEET = {
    "PV1": 2, "U3": 2, "BT1": 2, "F1": 2, "SW1": 2, "TB1": 2, "TB2": 2, "U2": 2, "J1": 2,
    "K1": 3, "K2": 3, "K3": 3, "M1": 3, "M2": 3, "M3": 3, "D1": 3, "D2": 3, "D3": 3,
    "C1": 3, "C2": 3, "C3": 3, "R1": 3, "R2": 3, "R3": 3,
    "U1": 4, "S1": 4, "S2": 4, "S3": 4, "S4": 4,
    **{f"R{n}": 4 for n in range(4, 12)},
    "Q1": 5, "Q2": 5, "Q3": 5, "R12": 5, "R13": 5, "R14": 5,
}


def disp(pin: str) -> str:
    """Pin name as printed on the drawing (ASCII '-' becomes a real minus sign)."""
    return pin.replace("-", "−")


# ----------------------------------------------------------------------------
# Netlist (Step 1). One entry per physical wire or component lead in CONNECTIONS.md.
# ----------------------------------------------------------------------------
@dataclass
class Conn:
    id: str
    net: str
    a: tuple            # (part, pin)
    b: tuple | None     # (part, pin), or None for "not connected"
    colour: str         # wire colour as written in CONNECTIONS.md
    gauge: str
    section: str
    draw: str           # colour key used on the drawing
    variant: str = "main"
    note: str = ""
    sheets: set = field(default_factory=set)


NETLIST: list[Conn] = []


def conn(net, a, b, colour, gauge, section, draw=None, variant="main", note=""):
    cid = f"W{len(NETLIST) + 1:03d}"
    pa = tuple(a.split(".", 1))
    pb = tuple(b.split(".", 1)) if b else None
    if draw is None:
        draw = colour.split()[0].lower()
    NETLIST.append(Conn(cid, net, pa, pb, colour, gauge, section, draw, variant, note))
    return cid


LEAD = "component lead"

# §3.1 / §4 / §5: battery, fuse, controller, main switch
conn("BAT+_UNFUSED", "BT1.+", "F1.1", "Red", "14 AWG", "§3.1 §4 §5", note="F1 within 15 cm of battery +")
conn("BAT+", "F1.2", "U3.BAT+", "Red", "14 AWG", "§3.1 §4 §5")
conn("BAT-", "BT1.-", "U3.BAT-", "Black", "14 AWG", "§3.1 §5", note="Battery − goes ONLY to BAT−; no wire to GND bus (§9)")
conn("PV+", "PV1.+", "U3.PV+", "Red", "18 AWG", "§3.1")
conn("PV-", "PV1.-", "U3.PV-", "Black", "18 AWG", "§3.1")
conn("LOAD+", "U3.LOAD+", "SW1.1", "Red", "14 AWG", "§3.1 §4", note="Switch is on the LOAD line, not the battery line")
conn("+12V_BUS", "SW1.2", "TB1.+", "Red", "14 AWG", "§4")
conn("GND_BUS", "U3.LOAD-", "TB2.GND", "Black", "14 AWG", "§3.1 §9")

# §6.1 / §6.2: three identical relay + motor channels
CHANNELS = [  # (n, GPIO, load description)
    (1, "GPIO26", "LEFT propulsion motor"),
    (2, "GPIO27", "RIGHT propulsion motor"),
    (3, "GPIO25", "CONVEYOR motor"),
]
for n, gpio, _ in CHANNELS:
    K, M, D, C, R = f"K{n}", f"M{n}", f"D{n}", f"C{n}", f"R{n}"
    conn("+12V_BUS", "TB1.+", f"{K}.DC+", "Red", "20–22 AWG", "§6.1")
    conn("GND_BUS", "TB2.GND", f"{K}.DC-", "Black", "20–22 AWG", "§6.1 §9")
    conn(f"{K}_IN", f"U1.{gpio}", f"{K}.IN", "Yellow", "22–24 AWG", "§6.1 §2.2 §2.3")
    conn(f"{K}_IN", f"{K}.IN", f"{R}.1", "— (resistor lead)", LEAD, "§6.1", draw="yellow",
         note="10 kΩ pull-down, soldered at the module IN / DC− terminals")
    conn("GND_BUS", f"{R}.2", f"{K}.DC-", "— (resistor lead)", LEAD, "§6.1 §9", draw="black",
         note="§6.1 table says 'to ground bus'; §6.1 text says solder at the IN/DC− terminals (DC− is on the bus)")
    conn("+12V_BUS", "TB1.+", f"{K}.COM", "Red", "16–18 AWG", "§6.2")
    conn(f"{M}+", f"{K}.NO", f"{M}.+", "Red", "16–18 AWG", "§6.2")
    conn("GND_BUS", f"{M}.-", "TB2.GND", "Black", "16–18 AWG", "§6.2 §9")
    conn(f"{M}+", f"{D}.K", f"{M}.+", "— (diode lead)", LEAD, "§6.2", draw="red",
         note="Band (cathode) on motor + / NO wire; solder at the motor")
    conn("GND_BUS", f"{D}.A", f"{M}.-", "— (diode lead)", LEAD, "§6.2", draw="black",
         note="Plain end (anode) on motor − / GND")
    conn(f"{M}+", f"{C}.1", f"{M}.+", "— (capacitor lead)", LEAD, "§6.2", draw="red",
         note="Optional, no polarity; solder at the motor")
    conn("GND_BUS", f"{C}.2", f"{M}.-", "— (capacitor lead)", LEAD, "§6.2", draw="black",
         note="Optional, no polarity; solder at the motor")
    conn("NC", f"{K}.NC", None, "—", "—", "§6.2", draw="black", note="NC not connected (never use NC)")

# §7: 5 V supply
conn("+12V_BUS", "TB1.+", "U2.IN+", "Red", "20–22 AWG", "§7")
conn("GND_BUS", "TB2.GND", "U2.IN-", "Black", "20–22 AWG", "§7 §9")
conn("+5V_UBEC", "U2.5V", "J1.1", "Orange", "22 AWG", "§7", note="Unpluggable link for USB programming")
conn("+5V", "J1.2", "U1.VIN", "Orange", "22 AWG", "§7 §2.3", note="VIN (sometimes labelled 5V); never 3V3")
SENSORS = [  # (n, position, TRIG GPIO, ECHO GPIO)
    (1, "Front-Left", "GPIO16", "GPIO34"),
    (2, "Front-Right", "GPIO17", "GPIO35"),
    (3, "Back-Left", "GPIO18", "GPIO36"),
    (4, "Back-Right", "GPIO19", "GPIO39"),
]
for n, *_ in SENSORS:
    conn("+5V", "J1.2", f"S{n}.VCC", "Orange", "22 AWG", "§7 §8")
conn("GND_BUS", "U2.GND", "TB2.GND", "Black", "22 AWG", "§7 §9")
conn("GND_BUS", "U1.GND", "TB2.GND", "Black", "22 AWG", "§7 §9 §2.3")
for n, *_ in SENSORS:
    conn("GND_BUS", f"S{n}.GND", "TB2.GND", "Black", "22 AWG", "§7 §8 §9")

# §8: sensors and ECHO dividers
for n, pos, trig, echo in SENSORS:
    Sx, Rt, Rb = f"S{n}", f"R{n + 3}", f"R{n + 7}"
    conn(f"TRIG{n}", f"U1.{trig}", f"{Sx}.TRIG", "Green", "22–24 AWG", "§8 §2.2", note="Direct, no divider")
    conn(f"ECHO{n}_5V", f"{Sx}.ECHO", f"{Rt}.1", "Blue", "22–24 AWG", "§8", note="0 / 5 V side")
    conn(f"ECHO{n}", f"{Rt}.2", f"U1.{echo}", "Blue", "22–24 AWG", "§8", note="0 / 3.33 V side")
    conn(f"ECHO{n}", f"U1.{echo}", f"{Rb}.1", "— (resistor lead)", LEAD, "§8", draw="blue")
    conn("GND_BUS", f"{Rb}.2", "TB2.GND", "— (resistor lead)", LEAD, "§8 §9", draw="black")

# §6.4: transistor fallback (alternative to the direct GPIO → IN wiring of a channel)
for n, gpio, _ in CHANNELS:
    Q, Rb, R = f"Q{n}", f"R{11 + n}", f"R{n}"
    conn(f"FB{n}_GPIO", f"U1.{gpio}", f"{Rb}.1", "Yellow (relay signal, §1)", "22–24 AWG", "§6.4", variant="fallback")
    conn(f"FB{n}_BASE", f"{Rb}.2", f"{Q}.B", "— (resistor lead)", LEAD, "§6.4", draw="yellow", variant="fallback")
    conn(f"FB{n}_GPIO", f"U1.{gpio}", f"{R}.1", "— (resistor lead)", LEAD, "§6.4", draw="yellow", variant="fallback",
         note="10 kΩ pull-down moves from IN to the GPIO side")
    conn("GND_BUS", f"{R}.2", "TB2.GND", "— (resistor lead)", LEAD, "§6.4", draw="black", variant="fallback")
    conn(f"FB{n}_IN", f"{Q}.C", f"K{n}.IN", "— (not specified)", "22–24 AWG", "§6.4", draw="yellow", variant="fallback")
    conn("GND_BUS", f"{Q}.E", "TB2.GND", "— (not specified)", "—", "§6.4", draw="black", variant="fallback")

CONN = {c.id: c for c in NETLIST}
PIN_NETS: dict[tuple, set] = defaultdict(set)
for _c in NETLIST:
    PIN_NETS[_c.a].add(_c.net)
    if _c.b:
        PIN_NETS[_c.b].add(_c.net)


def find(a, b, variant="main"):
    """Netlist id of the connection between pins a and b ('PART.PIN' strings)."""
    pa, pb = tuple(a.split(".", 1)), tuple(b.split(".", 1)) if b else None
    hits = [c.id for c in NETLIST if c.variant == variant and {c.a, c.b} == {pa, pb}]
    if len(hits) != 1:
        raise KeyError(f"netlist lookup {a} — {b} ({variant}): {hits}")
    return hits[0]


# ----------------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------------
EPS = 1e-6


def P(xy):
    return (round(float(xy[0]), 3), round(float(xy[1]), 3))


def on_segment(p, a, b, tol=1e-3):
    (px, py), (ax, ay), (bx, by) = p, a, b
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > tol * max(1.0, abs(bx - ax) + abs(by - ay)):
        return False
    return min(ax, bx) - tol <= px <= max(ax, bx) + tol and min(ay, by) - tol <= py <= max(ay, by) + tol


def segs_touch(a1, b1, a2, b2):
    """True if two closed segments share at least one point."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    o1, o2, o3, o4 = orient(a1, b1, a2), orient(a1, b1, b2), orient(a2, b2, a1), orient(a2, b2, b1)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on_segment(a2, a1, b1)) or (o2 == 0 and on_segment(b2, a1, b1)) or \
           (o3 == 0 and on_segment(a1, a2, b2)) or (o4 == 0 and on_segment(b1, a2, b2))


def seg_hits_rect(a, b, r):
    """True if segment a-b passes through the open rectangle r=(x0,y0,x1,y1)."""
    x0, y0, x1, y1 = r
    if x1 <= x0 or y1 <= y0:
        return False
    # Liang–Barsky clip against the open box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q <= 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return t1 - t0 > 1e-6


def text_w(s, fs=FS, bold=False):
    """Rendered width of a single-line string, in schematic units."""
    if not s:
        return 0.0
    tp = TextPath((0, 0), s, size=fs, prop=FONTPROP_B if bold else FONTPROP)
    return tp.get_extents().width * 25.4 / 72.0 / S


def text_w_mm(s, fs=FS, bold=False):
    return text_w(s, fs, bold) * S


def line_h(fs=FS):
    """Line pitch in units for a given font size."""
    return fs * 1.3 * 25.4 / 72.0 / S


def wrap(text, width, fs=FS, bold=False, measure=text_w):
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        trial = f"{cur} {w}" if cur else w
        if measure(trial, fs, bold) <= width * 0.96 or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ----------------------------------------------------------------------------
# Custom schemdraw elements
# ----------------------------------------------------------------------------
class Block(elm.Element):
    """Rectangular module (IC-style) with named pins on given sides.

    pins: list of (name, side, offset, label). offset is measured from the top edge
    for L/R pins and from the left edge for T/B pins. The pin's free end is its anchor.
    """

    def __init__(self, w, h, pins, lead=0.6, fill=BODY_FILL, center=None, center_fs=FS, **kw):
        super().__init__(**kw)
        self.elmparams["theta"] = 0
        self.w, self.h = w, h
        self.segments.append(SegmentPoly([(0, 0), (w, 0), (w, h), (0, h)], fill=fill, lw=1.4, color=PART_C))
        pad = 0.15
        for name, side, off, label in pins:
            label = disp(name) if label is None else label
            if side == "L":
                a, z, t, al, rot = (0, h - off), (-lead, h - off), (pad, h - off), ("left", "center"), 0
            elif side == "R":
                a, z, t, al, rot = (w, h - off), (w + lead, h - off), (w - pad, h - off), ("right", "center"), 0
            elif side == "T":
                a, z, t, al, rot = (off, h), (off, h + lead), (off, h - pad), ("center", "top"), 0
            else:
                a, z, t, al, rot = (off, 0), (off, -lead), (off, pad), ("center", "bottom"), 0
            self.segments.append(Segment([a, z], lw=1.3, color=PART_C))
            if label:
                self.segments.append(SegmentText(t, label, align=al, fontsize=FS_PIN, rotation=rot, color=PART_C))
            self.anchors[name] = z
        if center:
            lines = center.split("\n")
            lh = line_h(center_fs)
            y0 = h / 2 + (len(lines) - 1) * lh / 2
            for i, ln in enumerate(lines):
                bold = ln.startswith("**")
                ln = ln.strip("*")
                self.segments.append(SegmentText((w / 2, y0 - i * lh), ln, align=("center", "center"),
                                                 fontsize=center_fs + (1.5 if bold else 0), color=PART_C))
        self.anchors["start"] = (0, 0)


class Flag(elm.Element):
    """Off-sheet connector: arrow-shaped tag attached at (0,0).

    direction='right' means the tag body extends to the right of the attach point.
    """

    def __init__(self, text, direction="right", color=PART_C, **kw):
        super().__init__(**kw)
        self.elmparams["theta"] = 0
        w = text_w(text) + 0.45
        h = 0.55
        tip = 0.3
        sgn = 1 if direction == "right" else -1
        verts = [(0, 0), (sgn * tip, h / 2), (sgn * (tip + w), h / 2), (sgn * (tip + w + tip), 0),
                 (sgn * (tip + w), -h / 2), (sgn * tip, -h / 2)]
        self.segments.append(SegmentPoly(verts, fill="white", lw=1.1, color=color))
        self.segments.append(SegmentText((sgn * (tip + w / 2), 0), text, align=("center", "center"),
                                         fontsize=FS, color=PART_C))
        self.anchors["start"] = (0, 0)
        self.length = 2 * tip + w


class Link(elm.Element):
    """2-pin removable link / jumper (J1): pins '1' at (0,0) and '2' at (L,0)."""

    def __init__(self, L=2.0, **kw):
        super().__init__(**kw)
        self.elmparams["theta"] = 0
        r = 0.13
        x1, x2 = 0.55, L - 0.55
        self.segments.append(Segment([(0, 0), (x1 - r, 0)], color=PART_C))
        self.segments.append(Segment([(x2 + r, 0), (L, 0)], color=PART_C))
        self.segments.append(SegmentCircle((x1, 0), r, color=PART_C, fill="white"))
        self.segments.append(SegmentCircle((x2, 0), r, color=PART_C, fill="white"))
        # removable shorting link drawn as a bridge above the two pins
        self.segments.append(Segment([(x1, r), (x1, 0.45), (x2, 0.45), (x2, r)], color=PART_C, lw=1.6))
        self.anchors["1"] = (0, 0)
        self.anchors["2"] = (L, 0)
        self.anchors["start"] = (0, 0)


class BusBar(elm.Element):
    """Vertical bus bar / terminal strip. Terminals are created by Sheet.busbar()."""

    def __init__(self, h, color, w=0.38, **kw):
        super().__init__(**kw)
        self.elmparams["theta"] = 0
        self.segments.append(SegmentPoly([(0, 0), (w, 0), (w, h), (0, h)], fill=color, color=color, lw=1.0))
        self.anchors["start"] = (0, 0)


class Polyline(elm.Element):
    """A wire drawn as one joined path (absolute coordinates, placed at the origin)."""

    def __init__(self, pts, color, lw, zorder=2, **kw):
        super().__init__(**kw)
        self.elmparams["theta"] = 0
        self.segments.append(Segment(pts, color=color, lw=lw, capstyle="round", joinstyle="round", zorder=zorder))
        self.anchors["start"] = (0, 0)


def body_rect(e):
    """(x0, y0, x1, y1) of the drawn body: module blocks exclude their pin leads."""
    if isinstance(e, Block):
        x, y = e.absanchors["start"]
        return (x, y, x + e.w, y + e.h)
    bb = e.get_bbox(transform=True)
    return (bb.xmin, bb.ymin, bb.xmax, bb.ymax)


# ----------------------------------------------------------------------------
# Sheet: one A3 page = page axes (mm) + schematic axes (units) + bookkeeping
# ----------------------------------------------------------------------------
class Sheet:
    def __init__(self, num, title, heading, of=N_SHEETS, record=True):
        self.num, self.title, self.heading, self.of, self.record = num, title, heading, of, record
        self.fig = plt.figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))
        self.pax = self.fig.add_axes([0, 0, 1, 1])
        self.pax.set_xlim(0, PAGE_W)
        self.pax.set_ylim(0, PAGE_H)
        self.pax.axis("off")
        x0, y0, x1, y1 = INNER
        self.sax = self.fig.add_axes([x0 / PAGE_W, y0 / PAGE_H, (x1 - x0) / PAGE_W, (y1 - y0) / PAGE_H])
        self.sax.set_xlim(0, SW)
        self.sax.set_ylim(0, SH)
        self.sax.axis("off")
        self.sax.patch.set_alpha(0)
        self.d = schemdraw.Drawing(canvas=self.sax, show=False)
        self.term: dict[tuple, set] = defaultdict(set)   # point -> {(part, pin)} terminals at that point
        self.pinpos: dict[tuple, list] = defaultdict(list)
        self.bodies: list = []          # (name, element)
        self.wires: list[dict] = []
        self.parts: set = set()
        self.drawn: set = set()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.mytexts: list = []         # free texts placed by this script (checked against bodies)
        self.keepout: list = []         # (x0,y0,x1,y1) units: legend/notes/title block
        self.allowed_unconnected: set = set()
        draw_frame(self)

    # ---- parts and terminals -------------------------------------------------
    def part(self, ref, e, pins):
        """Add a component; pins maps pin name -> anchor name."""
        assert ref in PARTS, ref
        self.d.add(e)
        for pin, anchor in pins.items():
            pt = P(e.absanchors[anchor])
            self.term[pt].add((ref, pin))
            self.pinpos[(ref, pin)].append(pt)
        self.bodies.append((ref, e))
        self.parts.add(ref)
        return e

    def pin(self, ref, pin, i=0):
        return self.pinpos[(ref, pin)][i]

    def gnd(self, xy):
        """Ground symbol = connection to GND_BUS (TB2)."""
        e = self.d.add(elm.Ground().at(xy).theta(0).color(PART_C))
        self.term[P(xy)].add(("TB2", "GND"))
        self.bodies.append(("⏚", e))
        return e

    def vdd(self, xy, label, rep):
        e = self.d.add(elm.Vdd().at(xy).theta(0).color(PART_C).label(label, fontsize=FS))
        self.term[P(xy)].add(rep)
        self.bodies.append((label, e))
        return e

    def flag(self, xy, text, reps, direction="right", color=PART_C):
        e = self.d.add(Flag(text, direction=direction, color=color).at(xy))
        for r in reps:
            self.term[P(xy)].add(r)
        self.bodies.append((f"flag {text}", e))
        return e

    def nc(self, ref, pin, cid=None, label=None, side="right", color=PART_C):
        pt = self.pin(ref, pin)
        self.d.add(elm.NoConnect().at(pt).color(color))
        if cid:
            self.drawn.add(cid)
            if self.record:
                CONN[cid].sheets.add(self.num)
        else:
            self.allowed_unconnected.add((ref, pin))
        if label:
            dx = 0.3 if side == "right" else -0.3
            self.text((pt[0] + dx, pt[1]), label, ha="left" if side == "right" else "right", color=color)

    def busbar(self, ref, x, y0, y1, color, left_taps, right_taps, pin):
        """Vertical bus bar from y0 to y1 at x; returns dict of terminal points."""
        w = 0.38
        e = self.d.add(BusBar(y1 - y0, color, w=w).at((x, y0)))
        self.bodies.append((ref, e))
        self.parts.add(ref)
        pts = {}
        for key, y in left_taps.items():
            pts[key] = P((x, y))
        for key, y in right_taps.items():
            pts[key] = P((x + w, y))
        for key, pt in pts.items():
            self.term[pt].add((ref, pin))
            self.pinpos[(ref, pin)].append(pt)
            self.sax.add_patch(patches.Circle(pt, 0.09, facecolor="white", edgecolor=PART_C, lw=0.8, zorder=6))
        return pts

    # ---- wires ---------------------------------------------------------------
    def wire(self, cids, pts, g=None, gtext=None):
        """Draw one wire path for one or more netlist entries that share it.

        g = (segment index, side, frac) places the gauge label; side in above/below/left/right.
        """
        cids = [cids] if isinstance(cids, str) else list(cids)
        conns = [CONN[c] for c in cids]
        nets = {c.net for c in conns}
        draws = {c.draw for c in conns}
        if len(nets) != 1 or len(draws) != 1:
            self.errors.append(f"wire {cids}: mixes nets {nets}")
        pts = [P(p) for p in pts]
        w = dict(cids=cids, net=conns[0].net, draw=conns[0].draw, gauge=conns[0].gauge, pts=pts)
        self.wires.append(w)
        if g:
            seg, side, frac = (g + (0.5,))[:3] if len(g) == 2 else g
            a, b = pts[seg], pts[seg + 1]
            mx, my = a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac
            txt = gtext or (conns[0].gauge if len(conns) == 1 else f"{len(conns)} × {conns[0].gauge}")
            off = 0.32
            if side == "above":
                self.text((mx, my + off), txt, ha="center", fs=FS, color=GAUGE_C, style="italic")
            elif side == "below":
                self.text((mx, my - off), txt, ha="center", fs=FS, color=GAUGE_C, style="italic")
            elif side == "left":
                self.text((mx - 0.22, my), txt, ha="right", fs=FS, color=GAUGE_C, style="italic")
            else:
                self.text((mx + 0.22, my), txt, ha="left", fs=FS, color=GAUGE_C, style="italic")
        return w

    # ---- text ----------------------------------------------------------------
    def text(self, xy, s, ha="left", va="center", fs=FS, color=NOTE_C, weight="normal", style="normal",
             rotation=0, check=True):
        t = self.sax.text(xy[0], xy[1], s, ha=ha, va=va, fontsize=fs, color=color, fontweight=weight,
                          fontstyle=style, rotation=rotation, linespacing=1.2, zorder=8)
        if check:
            self.mytexts.append(t)
        return t

    def note(self, x, ytop, w, paras, title=None, color=NOTE_C, edge="#888888", fill="#fbfbf8", fs=FS,
             title_color=None, lw=0.8):
        """Boxed note; paras = list of strings, or (prefix, text) for hanging-indent items."""
        lh = line_h(fs)
        pad = 0.3
        inner = w - 2 * pad
        rows = []   # (prefix or None, x_offset, text, bold, colour)
        if title:
            for ln in wrap(title, inner, fs, True):
                rows.append((None, 0, ln, True, title_color or color))
        for p in paras:
            prefix, body = p if isinstance(p, tuple) else (None, p)
            ind = text_w(prefix, fs, True) + 0.28 if prefix else 0
            for i, ln in enumerate(wrap(body, inner - ind, fs)):
                rows.append((prefix if i == 0 else None, ind, ln, False, color))
        h = len(rows) * lh + 2 * pad - (lh - fs * 25.4 / 72 / S) * 0.5
        self.sax.add_patch(patches.FancyBboxPatch((x, ytop - h), w, h, boxstyle="round,pad=0,rounding_size=0.15",
                                                  facecolor=fill, edgecolor=edge, lw=lw, zorder=1))
        y = ytop - pad - lh * 0.4
        for prefix, dx, ln, bold, col in rows:
            if prefix:
                self.text((x + pad, y), prefix, fs=fs, color=col, weight="bold", check=False)
            self.text((x + pad + dx, y), ln, fs=fs, color=col, weight="bold" if bold else "normal", check=False)
            y -= lh
        self.keepout.append((x, ytop - h, x + w, ytop))
        return ytop - h

    def legend(self, x, ytop, w=17.6):
        """Wire colour legend (in units)."""
        items = [
            ("red", "+12 V (BAT+, LOAD+, +12V_BUS, motor +)"),
            ("black", "GND / negative (GND_BUS, BAT−, PV−)"),
            ("orange", "+5 V (UBEC → J1 → VIN, sensor VCC)"),
            ("yellow", "Relay signal (GPIO → FC65 IN)"),
            ("green", "Sensor TRIG"),
            ("blue", "Sensor ECHO"),
        ]
        lh = line_h(FS) * 1.08
        h = (len(items) + 4) * lh + 0.55
        self.sax.add_patch(patches.Rectangle((x, ytop - h), w, h, facecolor="white", edgecolor="#777777",
                                             lw=0.8, zorder=1))
        y = ytop - 0.35 - lh * 0.3
        self.text((x + 0.3, y), "Wire colour legend (CONNECTIONS.md §1)", weight="bold", check=False)
        y -= lh
        for key, label in items:
            if key == "yellow":
                self.sax.plot([x + 0.35, x + 1.9], [y, y], color=YELLOW_EDGE, lw=3.2, solid_capstyle="round", zorder=2)
            self.sax.plot([x + 0.35, x + 1.9], [y, y], color=WIRE[key], lw=2.2, solid_capstyle="round", zorder=3)
            self.text((x + 2.2, y), label, check=False)
            y -= lh
        self.text((x + 0.3, y), "Line weight follows gauge: 14 AWG thickest … 22–24 AWG thinnest.", check=False)
        y -= lh
        self.sax.add_patch(patches.Circle((x + 1.1, y), 0.11, color=PART_C, zorder=3))
        self.text((x + 2.2, y), "Junction (wires joined). No dot = no connection.", check=False)
        y -= lh
        gx = x + 1.1
        self.sax.plot([gx, gx], [y + 0.22, y], color=PART_C, lw=1.2)
        for dx, dy in ((0.25, 0), (0.16, -0.1), (0.07, -0.2)):
            self.sax.plot([gx - dx, gx + dx], [y + dy, y + dy], color=PART_C, lw=1.2)
        self.text((x + 2.2, y), "Wire to GND_BUS (TB2, star point, Sheet 2)", check=False)
        self.keepout.append((x, ytop - h, x + w, ytop))
        return ytop - h

    # ---- finishing -----------------------------------------------------------
    def compute_dots(self):
        dots = []
        bynet = defaultdict(list)
        colour = {}
        seen = set()
        for w in self.wires:
            key = (w["net"], tuple(w["pts"]))
            if key in seen:
                continue
            seen.add(key)
            colour[w["net"]] = w["draw"]
            for a, b in zip(w["pts"], w["pts"][1:]):
                if a != b:
                    bynet[w["net"]].append((a, b))
        for net, segs in bynet.items():
            verts = {p for s in segs for p in s}
            edges = set()
            for a, b in segs:
                on = sorted((v for v in verts if on_segment(v, a, b)),
                            key=lambda v: (v[0] - a[0]) ** 2 + (v[1] - a[1]) ** 2)
                for u, v in zip(on, on[1:]):
                    if u != v:
                        edges.add(frozenset((u, v)))
            deg = Counter(v for e in edges for v in e)
            for v in verts:
                if deg[v] + (1 if self.term.get(v) else 0) >= 3:
                    dots.append((v, colour[net]))
        return dots

    def finish(self):
        for w in self.wires:
            c = WIRE[w["draw"]]
            lw = GAUGE_LW.get(w["gauge"], 1.3)
            if w["draw"] == "yellow":
                self.d.add(Polyline(w["pts"], YELLOW_EDGE, lw + 1.1, zorder=2).at((0, 0)))
            self.d.add(Polyline(w["pts"], c, lw, zorder=3).at((0, 0)))
        for v, col in self.compute_dots():
            fc = YELLOW_EDGE if col == "yellow" else WIRE[col]
            self.d.add(elm.Dot(radius=0.13).at(v).color(fc).zorder(5))
        self.d.draw(show=False)
        self.sax.set_xlim(0, SW)
        self.sax.set_ylim(0, SH)
        self.sax.set_aspect("auto")
        self.check()

    # ---- verification ----------------------------------------------------------
    def check(self):
        err = self.errors
        for w in self.wires:
            s, e = w["pts"][0], w["pts"][-1]
            for cid in w["cids"]:
                c = CONN[cid]
                ok = (c.a in self.term.get(s, ()) and c.b in self.term.get(e, ())) or \
                     (c.b in self.term.get(s, ()) and c.a in self.term.get(e, ()))
                if not ok:
                    err.append(f"{cid} {c.a}->{c.b}: wire ends at {sorted(self.term.get(s, ()))} / "
                               f"{sorted(self.term.get(e, ()))}")
                self.drawn.add(cid)
                if self.record:
                    c.sheets.add(self.num)
            for a, b in zip(w["pts"], w["pts"][1:]):
                if a[0] != b[0] and a[1] != b[1]:
                    self.warnings.append(f"{w['cids']}: diagonal segment {a}-{b}")
        segs = [(w["net"], a, b) for w in self.wires for a, b in zip(w["pts"], w["pts"][1:]) if a != b]
        for (n1, a1, b1), (n2, a2, b2) in itertools.combinations(segs, 2):
            if n1 != n2 and segs_touch(a1, b1, a2, b2):
                err.append(f"nets {n1} and {n2} touch/cross near {a1}-{b1} / {a2}-{b2}")
        for net, a, b in segs:
            for pt, reps in self.term.items():
                if on_segment(pt, a, b):
                    nets = set().union(*(PIN_NETS.get(r, set()) for r in reps))
                    if net not in nets:
                        err.append(f"net {net} wire touches terminal {sorted(reps)} at {pt}")
        for name, e in self.bodies:
            bb = e.get_bbox(transform=True)
            r = (bb.xmin + 0.06, bb.ymin + 0.06, bb.xmax - 0.06, bb.ymax - 0.06)
            for net, a, b in segs:
                if seg_hits_rect(a, b, r):
                    err.append(f"wire of net {net} ({a}-{b}) passes through body of {name}")
        self.check_text()

    def check_text(self):
        self.fig.canvas.draw()
        rend = self.fig.canvas.get_renderer()
        texts = [t for ax in (self.pax, self.sax) for t in ax.texts if t.get_visible() and t.get_text().strip()]
        bbs = [(t, t.get_window_extent(rend)) for t in texts]
        fig_bb = self.fig.bbox
        frame = self.sax.get_window_extent(rend)
        for t, bb in bbs:
            if bb.x0 < fig_bb.x0 or bb.y0 < fig_bb.y0 or bb.x1 > fig_bb.x1 or bb.y1 > fig_bb.y1:
                self.errors.append(f"text clipped by page: {t.get_text()!r}")
            if t.axes is self.sax and (bb.x0 < frame.x0 - 0.5 or bb.y0 < frame.y0 - 0.5 or
                                       bb.x1 > frame.x1 + 0.5 or bb.y1 > frame.y1 + 0.5):
                self.errors.append(f"text crosses the drawing frame: {t.get_text()!r}")
        for (t1, b1), (t2, b2) in itertools.combinations(bbs, 2):
            ox = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
            oy = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
            if ox > 1.0 and oy > 1.0:
                self.warnings.append(f"text overlap: {t1.get_text()[:40]!r} / {t2.get_text()[:40]!r}")
        tr = self.sax.transData
        wsegs = [(w["net"], tr.transform(a), tr.transform(b)) for w in self.wires
                 for a, b in zip(w["pts"], w["pts"][1:])]
        for t, bb in bbs:
            r = (bb.x0 + 1.0, bb.y0 + 1.0, bb.x1 - 1.0, bb.y1 - 1.0)
            for net, a, b in wsegs:
                if seg_hits_rect(a, b, r):
                    self.warnings.append(f"text over wire ({net}): {t.get_text()[:40]!r}")
                    break
        mine = {id(t) for t in self.mytexts}
        for name, e in self.bodies:
            bx0, by0, bx1, by1 = body_rect(e)
            (x0, y0), (x1, y1) = tr.transform((bx0, by0)), tr.transform((bx1, by1))
            for t, tb in bbs:
                if id(t) not in mine:
                    continue
                if min(tb.x1, x1) - max(tb.x0, x0) > 1.5 and min(tb.y1, y1) - max(tb.y0, y0) > 1.5:
                    self.warnings.append(f"text {t.get_text()[:40]!r} overlaps symbol {name}")
        # keep-out zones (title block, legend, notes): nothing from the schematic may enter
        tb_u = (TB_U[0], 0, SW, TB_U[1])
        for net, a, b in [(w["net"], a, b) for w in self.wires for a, b in zip(w["pts"], w["pts"][1:])]:
            if seg_hits_rect(a, b, tb_u):
                self.errors.append(f"wire {net} enters the title block")
            for k in self.keepout:
                if seg_hits_rect(a, b, k):
                    self.errors.append(f"wire {net} passes through a note/legend box")
        for name, e in self.bodies:
            bx0, by0, bx1, by1 = body_rect(e)
            for k in self.keepout + [tb_u]:
                if bx1 > k[0] and bx0 < k[2] and by1 > k[1] and by0 < k[3]:
                    self.errors.append(f"symbol {name} overlaps a note/legend/title box")
        for k1, k2 in itertools.combinations(self.keepout + [tb_u], 2):
            if min(k1[2], k2[2]) - max(k1[0], k2[0]) > 0.01 and min(k1[3], k2[3]) - max(k1[1], k2[1]) > 0.01:
                self.errors.append(f"note/legend/title boxes overlap: {k1} / {k2}")


# ----------------------------------------------------------------------------
# Frame, grid reference and title block (drawn in mm on the page axes)
# ----------------------------------------------------------------------------
def draw_frame(sh: Sheet):
    ax = sh.pax
    ox0, oy0, ox1, oy1 = OUTER
    ix0, iy0, ix1, iy1 = INNER
    ax.add_patch(patches.Rectangle((ox0, oy0), ox1 - ox0, oy1 - oy0, fill=False, lw=1.2, color=PART_C))
    ax.add_patch(patches.Rectangle((ix0, iy0), ix1 - ix0, iy1 - iy0, fill=False, lw=1.6, color=PART_C))
    cols, rows = 8, "ABCDEF"
    cw = (ix1 - ix0) / cols
    rh = (iy1 - iy0) / len(rows)
    for i in range(cols + 1):
        x = ix0 + i * cw
        for ya, yb in ((oy0, iy0), (iy1, oy1)):
            ax.plot([x, x], [ya, yb], color=PART_C, lw=0.8)
        if i < cols:
            for y in ((oy0 + iy0) / 2, (iy1 + oy1) / 2):
                ax.text(x + cw / 2, y, str(i + 1), ha="center", va="center", fontsize=9, color=GRID_C)
    for j in range(len(rows) + 1):
        y = iy1 - j * rh
        for xa, xb in ((ox0, ix0), (ix1, ox1)):
            ax.plot([xa, xb], [y, y], color=PART_C, lw=0.8)
        if j < len(rows):
            for x in ((ox0 + ix0) / 2, (ix1 + ox1) / 2):
                ax.text(x, y - rh / 2, rows[j], ha="center", va="center", fontsize=9, color=GRID_C)
    # sheet heading (top-left, inside the frame)
    ax.text(ix0 + 3, iy1 - 4.5, sh.heading, ha="left", va="center", fontsize=12.5, fontweight="bold", color=PART_C)
    # title block
    x0, y0, x1, y1 = TB
    ax.add_patch(patches.Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor="white", edgecolor=PART_C, lw=1.4,
                                   zorder=5))
    r1, r2, r3 = y1 - 13, y1 - 26, y1 - 36.5

    def hline(y, xa=x0, xb=x1):
        ax.plot([xa, xb], [y, y], color=PART_C, lw=0.9, zorder=6)

    def vline(x, ya, yb):
        ax.plot([x, x], [ya, yb], color=PART_C, lw=0.9, zorder=6)

    def cell(x, ytop, cap, val, fs=9.5, bold=False):
        ax.text(x + 1.8, ytop - 2.6, cap, ha="left", va="center", fontsize=FS, color="#666666", zorder=7)
        ax.text(x + 1.8, ytop - 7.0, val, ha="left", va="center", fontsize=fs, color=PART_C,
                fontweight="bold" if bold else "normal", zorder=7)

    hline(r1)
    hline(r2)
    hline(r3)
    cell(x0, y1, "PROJECT", PROJECT, fs=13, bold=True)
    cell(x0, r1, "SHEET TITLE", sh.title, fs=11, bold=True)
    xs = [x0, x0 + 40, x0 + 64, x0 + 112, x1]
    for xv in xs[1:-1]:
        vline(xv, r3, r2)
    cell(xs[0], r2, "SHEET", f"{sh.num} of {sh.of}", bold=True)
    cell(xs[1], r2, "REV", REV, bold=True)
    cell(xs[2], r2, "DATE", DATE)
    cell(xs[3], r2, "SIZE / SCALE", "A3 landscape · NTS")
    ax.text(x0 + 1.8, r3 - 2.9, f"Source: {SOURCE}", ha="left", va="center", fontsize=9.5, color=PART_C,
            fontweight="bold", zorder=7)
    ax.text(x0 + 1.8, r3 - 7.2, "Generated by tools/make_schematic.py — edit the netlist there, then re-run",
            ha="left", va="center", fontsize=FS, color="#555555", zorder=7)


# ----------------------------------------------------------------------------
# Sheet 2: power distribution
# ----------------------------------------------------------------------------
def sheet_power():
    sh = Sheet(2, "Power distribution", "SHEET 2 — POWER DISTRIBUTION   (§3, §4, §5, §7, §9)")
    d = sh.d

    # U3 charge controller
    u3 = Block(9.0, 17.0, [
        ("PV+", "L", 1.0, None), ("PV-", "L", 2.2, None),
        ("BAT+", "L", 9.0, None), ("BAT-", "L", 10.2, None),
        ("LOAD+", "R", 5.5, None), ("LOAD-", "R", 11.5, None),
    ], center="**U3\n30 A PWM solar\ncharge controller\n(LCD)\n\nBattery type: LI / USER\nLOAD mode: always ON\n"
               "(often “24H”)\nLVD 12.0 V on LOAD\nSettings: Sheet 6")
    sh.part("U3", u3.at((10.0, 17.5)), {"PV+": "PV+", "PV-": "PV-", "BAT+": "BAT+", "BAT-": "BAT-",
                                         "LOAD+": "LOAD+", "LOAD-": "LOAD-"})

    # PV1 solar panel
    pv = elm.Solar().endpoints((3.4, 30.5), (3.4, 33.5)).color(PART_C)
    sh.part("PV1", pv, {"-": "start", "+": "end"})
    sh.text((3.9, 33.1), "+", fs=9, weight="bold")
    sh.text((3.9, 30.9), "−", fs=9, weight="bold")
    sh.text((0.3, 35.6), "PV1  Solar panel 12 V 10 W\npoly, Vmp ≈ 18 V, Imp ≈ 0.55 A", va="center")
    sh.wire(find("PV1.+", "U3.PV+"), [sh.pin("PV1", "+"), sh.pin("U3", "PV+")], g=(0, "above", 0.55))
    sh.wire(find("PV1.-", "U3.PV-"), [sh.pin("PV1", "-"), (7.0, 30.5), (7.0, 32.3), sh.pin("U3", "PV-")],
            g=(0, "below", 0.5))

    # BT1 battery and F1 fuse
    bt = elm.Battery().endpoints((3.4, 22.6), (3.4, 20.0)).scale(1.6).color(PART_C)
    sh.part("BT1", bt, {"+": "start", "-": "end"})
    sh.text((2.6, 22.3), "+", fs=9, weight="bold", ha="right")
    sh.text((2.6, 20.3), "−", fs=9, weight="bold", ha="right")
    sh.text((0.3, 17.55), "BT1  LiFePO4 12.8 V 15 Ah\n4S1P IFR 32140FS, built-in BMS", va="center")
    f1 = elm.Fuse().endpoints((4.4, 25.5), (7.0, 25.5)).color(PART_C)
    sh.part("F1", f1, {"1": "start", "2": "end"})
    sh.text((5.7, 26.75), "F1  15 A blade fuse\nwithin 15 cm of BT1 +", ha="center", weight="bold", color=WARN_C)
    sh.wire(find("BT1.+", "F1.1"), [sh.pin("BT1", "+"), (3.4, 25.5), sh.pin("F1", "1")], g=(0, "left", 0.5))
    sh.wire(find("F1.2", "U3.BAT+"), [sh.pin("F1", "2"), sh.pin("U3", "BAT+")])
    sh.wire(find("BT1.-", "U3.BAT-"), [sh.pin("BT1", "-"), (3.4, 19.0), (8.0, 19.0), (8.0, 24.3),
                                       sh.pin("U3", "BAT-")], g=(1, "below", 0.5))
    sh.text((8.75, 25.85), "14 AWG", ha="center", fs=FS, color=GAUGE_C, style="italic")

    # SW1 on the LOAD+ line
    sw = elm.Switch().endpoints((21.0, 29.0), (23.4, 29.0)).color(PART_C)
    sh.part("SW1", sw, {"1": "start", "2": "end"})
    sh.text((22.2, 30.45), "SW1  ON/OFF\n≥ 15 A DC", ha="center", weight="bold")

    # bus bars
    tb1 = sh.busbar("TB1", 25.6, 28.4, 37.3, WIRE["red"], {"in": 29.0},
                    {"k1": 36.8, "k2": 35.6, "k3": 34.4, "dc": 33.2, "u2": 30.8}, "+")
    tb2_taps = {"u2in": 22.0, "u2gnd": 21.0, "m1": 20.0, "m2": 18.8, "m3": 17.6, "dc": 16.4,
                "u1": 15.2, "s": 14.0, "r811": 12.8}
    tb2 = sh.busbar("TB2", 25.6, 12.3, 23.6, WIRE["black"], {"in": 23.0}, tb2_taps, "GND")
    sh.text((25.3, 34.4), "TB1\n+12V_BUS\nterminal\nblock", ha="right", weight="bold", color=WIRE["red"])
    sh.text((25.3, 19.4), "TB2\nGND_BUS\nbus bar —\nSTAR POINT", ha="right", weight="bold")

    sh.wire(find("U3.LOAD+", "SW1.1"), [sh.pin("U3", "LOAD+"), sh.pin("SW1", "1")], g=(0, "above", 0.4))
    sh.wire(find("SW1.2", "TB1.+"), [sh.pin("SW1", "2"), tb1["in"]], g=(0, "below", 0.5))
    sh.wire(find("U3.LOAD-", "TB2.GND"), [sh.pin("U3", "LOAD-"), tb2["in"]], g=(0, "above", 0.5))

    # TB1 taps -> off-sheet flags
    fx = 28.6
    for n in (1, 2, 3):
        y = tb1[f"k{n}"][1]
        sh.flag((fx, y), f"K{n} COM · Sheet 3", [(f"K{n}", "COM")])
        sh.wire(find("TB1.+", f"K{n}.COM"), [tb1[f"k{n}"], (fx, y)], g=(0, "above", 0.5))
    y = tb1["dc"][1]
    sh.flag((fx, y), "K1, K2, K3 DC+ · Sheet 3", [(f"K{n}", "DC+") for n in (1, 2, 3)])
    sh.wire([find("TB1.+", f"K{n}.DC+") for n in (1, 2, 3)], [tb1["dc"], (fx, y)], g=(0, "above", 0.5),
            gtext="3 × 20–22")

    # TB2 taps -> off-sheet flags
    for n in (1, 2, 3):
        y = tb2[f"m{n}"][1]
        sh.flag((fx, y), f"M{n} − · Sheet 3", [(f"M{n}", "-")])
        sh.wire(find(f"M{n}.-", "TB2.GND"), [tb2[f"m{n}"], (fx, y)], g=(0, "above", 0.5))
    groups = [
        ("dc", "K1, K2, K3 DC− (+ R1–R3) · Sheet 3", [f"K{n}.DC-" for n in (1, 2, 3)], "3 × 20–22"),
        ("u1", "U1 GND · Sheet 4", ["U1.GND"], None),
        ("s", "S1–S4 GND · Sheet 4", [f"S{n}.GND" for n in (1, 2, 3, 4)], "4 × 22 AWG"),
        ("r811", "R8–R11 (2 kΩ) · Sheet 4", [f"R{n}.2" for n in (8, 9, 10, 11)], "gauge n/s"),
    ]
    for key, label, pins, gt in groups:
        y = tb2[key][1]
        reps = [tuple(p.split(".", 1)) for p in pins]
        sh.flag((fx, y), label, reps)
        sh.wire([find(p, "TB2.GND") for p in pins], [tb2[key], (fx, y)], g=(0, "above", 0.5), gtext=gt)

    # U2 UBEC and J1 link
    u2 = Block(5.0, 3.6, [("IN+", "L", 0.9, None), ("IN-", "L", 2.5, None), ("5V", "R", 0.9, "5V OUT"),
                          ("GND", "B", 2.5, "GND OUT")], center=None)
    sh.part("U2", u2.at((38.4, 20.9)), {"IN+": "IN+", "IN-": "IN-", "5V": "5V", "GND": "GND"})
    sh.text((38.4, 25.05), "U2  UBEC 5 V / 3 A", ha="left", weight="bold")
    yin, ymin = sh.pin("U2", "IN+")[1], sh.pin("U2", "IN-")[1]
    sh.wire(find("TB1.+", "U2.IN+"), [tb1["u2"], (36.6, 30.8), (36.6, yin), sh.pin("U2", "IN+")],
            g=(0, "above", 0.3))
    sh.wire(find("TB2.GND", "U2.IN-"), [tb2["u2in"], sh.pin("U2", "IN-")], g=(0, "above", 0.75))
    gx, gy = sh.pin("U2", "GND")
    sh.wire(find("U2.GND", "TB2.GND"), [tb2["u2gnd"], (35.0, 21.0), (35.0, 19.4), (gx, 19.4), (gx, gy)],
            g=(2, "below", 0.5))

    j1 = Link(2.0)
    y5 = sh.pin("U2", "5V")[1]
    sh.part("J1", j1.at((45.0, y5)), {"1": "1", "2": "2"})
    sh.text((46.0, y5 + 1.75), "J1  5 V link\nunplug for USB\nprogramming (§7)", ha="center", weight="bold", fs=FS)
    sh.wire(find("U2.5V", "J1.1"), [sh.pin("U2", "5V"), sh.pin("J1", "1")])
    jx = 48.0
    sh.flag((48.9, y5), "U1 VIN · Sheet 4", [("U1", "VIN")])
    sh.flag((48.9, y5 - 1.4), "S1–S4 VCC · Sheet 4", [(f"S{n}", "VCC") for n in (1, 2, 3, 4)])
    sh.wire(find("J1.2", "U1.VIN"), [sh.pin("J1", "2"), (48.9, y5)])
    sh.wire([find("J1.2", f"S{n}.VCC") for n in (1, 2, 3, 4)],
            [sh.pin("J1", "2"), (jx, y5), (jx, y5 - 1.4), (48.9, y5 - 1.4)])
    sh.text((47.9, y5 - 2.3), "+5V · orange 22 AWG", ha="left", fs=FS, color=GAUGE_C, style="italic")

    # call-outs and notes
    sh.note(0.3, 16.6, 19.0, [
        "Battery − reaches the common ground only THROUGH the controller (BAT− → LOAD−). Many PWM "
        "controllers switch the negative side internally; an extra wire bypasses the low-voltage "
        "disconnect and can damage U3 (§9).",
        "Star layout: heavy motor returns go straight to TB2, not daisy-chained through the ESP32 ground.",
    ], title="⚠ NO direct wire from BT1 − to GND_BUS (TB2)", color=NOTE_C, title_color=WARN_C, edge=WARN_C,
        fill="#fff6f6", lw=1.3)
    sh.note(19.9, 28.3, 5.3, ["SW1 is on the LOAD+ line, NOT the battery line (§4)."], color=WARN_C,
            edge=WARN_C, fill="#fff6f6", lw=1.0)
    sh.note(38.4, 37.4, 17.0, [
        ("1.", "Connect the battery to BAT+ / BAT− FIRST (F1 fitted, SW1 OFF). U3 powers up and detects 12 V."),
        ("2.", "THEN connect the solar panel to PV+ / PV− (panel covered or face-down)."),
        ("3.", "Then connect the loads to LOAD+ / LOAD−."),
        ("4.", "Disconnect in REVERSE: loads, then panel, then battery. Never leave the panel on U3 "
               "without the battery."),
    ], title="Connection order (§3.2)")
    sh.note(38.4, 18.4, 17.0, [
        "All loads (relays, motors, UBEC) connect to the LOAD terminals only, never directly to the battery (§3.1).",
        "Measure U2 output before connecting the ESP32: 4.9–5.2 V (§7).",
        "USB programming: SW1 OFF → unplug J1 → upload → unplug USB → refit J1 → SW1 ON (§7).",
        "Optional battery isolator (§4): a 2nd switch next to F1; open it only after unplugging the panel. "
        "Not drawn.",
    ], title="Notes")
    sh.legend(0.3, 6.3)
    sh.finish()
    return sh


# ----------------------------------------------------------------------------
# Sheet 3: relays and motors
# ----------------------------------------------------------------------------
def relay_block(n, jumper="H", lower_text="Jumper: H"):
    return Block(5.6, 3.0, [
        ("IN", "L", 1.5, None), ("DC+", "T", 1.0, None), ("DC-", "B", 1.0, None),
        ("COM", "R", 0.6, None), ("NO", "R", 1.5, None), ("NC", "R", 2.4, None),
    ], center=f"**K{n}\nFC65 relay\n{lower_text}")


def sheet_relays():
    sh = Sheet(3, "Relay and motor drive", "SHEET 3 — RELAY AND MOTOR DRIVE   (§6.1, §6.2)")
    rows = [32.3, 22.9, 13.5]
    motor_txt = {1: "LEFT propulsion motor", 2: "RIGHT propulsion motor", 3: "CONVEYOR motor"}
    for (n, gpio, _), yc in zip(CHANNELS, rows):
        K, M, D, C, R = f"K{n}", f"M{n}", f"D{n}", f"C{n}", f"R{n}"
        sh.text((0.3, yc + 1.55), f"CH{n} · {gpio} → {K} → {M}", weight="bold", fs=9)
        sh.text((0.3, yc + 0.95), motor_txt[n], fs=FS)
        # relay module
        k = relay_block(n)
        sh.part(K, k.at((8.6, yc - 1.5)), {p: p for p in ("IN", "DC+", "DC-", "COM", "NO", "NC")})
        xin, _ = sh.pin(K, "IN")
        # IN: off-sheet GPIO flag, pull-down
        fx = 4.9
        sh.flag((fx, yc), f"U1 {gpio} · Sheet 4", [("U1", gpio)], direction="left")
        nx = 6.3
        r = elm.Resistor().endpoints((nx, yc), (nx, yc - 2.1)).color(PART_C)
        sh.part(R, r, {"1": "start", "2": "end"})
        sh.text((nx - 0.35, yc - 1.05), f"{R}\n10 kΩ", ha="right")
        sh.wire(find(f"U1.{gpio}", f"{K}.IN"), [(fx, yc), sh.pin(K, "IN")])
        sh.text((fx + 0.75, yc + 0.32), "22–24 AWG", fs=FS, color=GAUGE_C, style="italic")
        sh.wire(find(f"{K}.IN", f"{R}.1"), [sh.pin(K, "IN"), sh.pin(R, "1")])
        # module supply
        px, py = sh.pin(K, "DC+")
        sh.vdd((px, py + 0.55), "+12V_BUS", ("TB1", "+"))
        sh.wire(find("TB1.+", f"{K}.DC+"), [(px, py + 0.55), (px, py)], g=(0, "left", 0.5))
        gx, gy = sh.pin(K, "DC-")
        sh.gnd((gx, gy - 0.55))
        sh.wire(find("TB2.GND", f"{K}.DC-"), [(gx, gy), (gx, gy - 0.55)], g=(0, "right", 0.5))
        sh.wire(find(f"{R}.2", f"{K}.DC-"), [sh.pin(R, "2"), (gx, gy)])
        # COM from the 12 V bus
        cx, cy = sh.pin(K, "COM")
        rx = cx + 0.9
        sh.vdd((rx, py + 0.55), "+12V_BUS", ("TB1", "+"))
        sh.wire(find("TB1.+", f"{K}.COM"), [(rx, py + 0.55), (rx, cy), (cx, cy)], g=(0, "right", 0.5))
        # NC not connected
        sh.nc(K, "NC", cid=find(f"{K}.NC", None), label="not connected")
        # motor with flyback diode and capacitor at its terminals
        mx, top, bot = 19.6, yc, yc - 3.0
        m = elm.Motor().endpoints((mx, top), (mx, bot)).color(PART_C)
        sh.part(M, m, {"+": "start", "-": "end"})
        sh.text((mx - 0.65, yc - 1.05), "+", fs=9, weight="bold", ha="right")
        sh.text((mx - 0.65, yc - 1.95), "−", fs=9, weight="bold", ha="right")
        sh.text((mx - 0.6, yc - 2.65), M, ha="right", weight="bold")
        dx_, cx_ = 21.8, 24.6
        dd = elm.Diode().endpoints((dx_, bot), (dx_, top)).scale(1.5).color(PART_C)
        sh.part(D, dd, {"A": "start", "K": "end"})
        cc = elm.Capacitor().endpoints((cx_, top), (cx_, bot)).color(PART_C)
        sh.part(C, cc, {"1": "start", "2": "end"})
        nox, noy = sh.pin(K, "NO")
        sh.wire(find(f"{K}.NO", f"{M}.+"), [(nox, noy), (mx, noy)], g=(0, "above", 0.62))
        sh.wire(find(f"{D}.K", f"{M}.+"), [sh.pin(D, "K"), sh.pin(M, "+")])
        sh.wire(find(f"{C}.1", f"{M}.+"), [sh.pin(C, "1"), sh.pin(M, "+")])
        sh.wire(find(f"{D}.A", f"{M}.-"), [sh.pin(D, "A"), sh.pin(M, "-")])
        sh.wire(find(f"{C}.2", f"{M}.-"), [sh.pin(C, "2"), sh.pin(M, "-")])
        sh.gnd((mx, bot - 0.6))
        sh.wire(find(f"{M}.-", "TB2.GND"), [sh.pin(M, "-"), (mx, bot - 0.6)], g=(0, "left", 0.5))
        sh.text((dx_ + 0.45, yc - 0.55), "band = K", fs=FS, color=WARN_C)
        sh.text((mx + 0.55, bot - 0.45), f"{D}  1N5408 / SB560", ha="left", va="top")
        sh.text((cx_ + 0.35, yc - 1.5), f"{C}\n0.1 µF\n≥ 50 V\n(optional)", ha="left")
    sh.text((0.3, rows[2] - 5.0), "Conveyor motor M3 is ASSUMED to be a 12 V DC brushed geared motor — VERIFY "
            "before wiring. If it is another voltage or AC it needs its own supply and must not use this "
            "circuit (§6.2).", fs=FS, color=WARN_C, weight="bold")

    # right column: diode detail and notes
    x0, w = 30.6, 24.8
    ytop = 37.3
    ytop = sh.note(x0, ytop, w, [
        ("•", "Band (cathode, K) → motor + = the wire from NO. Plain end (anode, A) → motor − = GND_BUS."),
        ("•", "In normal running the diode is reverse-biased; it only conducts the turn-off spike. Fitted "
              "the other way round it shorts 12 V the moment the relay closes and blows F1."),
        ("•", "Solder D1–D3 and C1–C3 directly on the motor terminals, short leads."),
        ("•", "Wrong spin direction? Swap that motor's two wires AT THE MOTOR, then move the diode so the "
              "band is still on the wire that comes from NO. The diode follows the wiring, not the “+” "
              "marking on the motor."),
    ], title="Flyback diode orientation — check every channel (§6.2)", title_color=WARN_C, edge=WARN_C,
        fill="#fff6f6", lw=1.2)
    ytop = sh.note(x0, ytop - 0.4, w, [
        ("•", "Trigger jumper on K1–K3 set to H (HIGH-level): GPIO HIGH = relay ON; GPIO LOW or no signal = "
              "OFF. Firmware assumes RELAY_ON_LEVEL HIGH (§6.1)."),
        ("•", "R1–R3 (10 kΩ) are soldered across the module's IN and DC− terminals (DC− is on GND_BUS), so IN "
              "stays LOW during boot, reset, flashing, or if the yellow wire falls off."),
        ("•", "Use NO only. NC stays unconnected: with no power or signal NO is open and the motor is OFF."),
        ("•", "Before connecting any IN wire to the ESP32: power the module from 12 V, jumper on H, nothing on "
              "IN; IN to DC− must read ≈ 0 V. 5–12 V means jumper on L or a pull-up to 12 V — do NOT "
              "connect (it would destroy the ESP32 pin)."),
        ("•", "Test 3.3 V triggering first (§6.3). If a module does not switch reliably, use the transistor "
              "fallback on Sheet 5."),
    ], title="Relay module notes (§6.1, §6.3)")

    # Detail drawing of the diode orientation (illustration only: symbols and leaders, no wiring)
    ytop -= 0.9
    sh.text((x0, ytop), "DETAIL — flyback diode orientation at each motor (D1, D2, D3) · illustration, not wiring",
            weight="bold", fs=9, check=False)
    lead = dict(arrowstyle="-|>,head_length=0.5,head_width=0.22", color="#555555", lw=0.8, shrinkA=2, shrinkB=1)
    for ok, x in ((True, 33.6), (False, 45.6)):
        y_lo, y_hi = ytop - 10.6, ytop - 2.4
        a, k = ((x, y_lo), (x, y_hi)) if ok else ((x, y_hi), (x, y_lo))
        dd = sh.d.add(elm.Diode().endpoints(a, k).scale(2.6).color(PART_C if ok else "#8c8c8c"))
        mid = (y_lo + y_hi) / 2
        bar = mid + (0.65 if ok else -0.65)
        if ok:
            sh.text((x, y_hi + 0.55), "✓ CORRECT", ha="center", weight="bold", color=WIRE["green"], check=False)
            sh.text((x + 0.5, y_hi - 0.3), "to motor +  =  wire from NO (red)", check=False)
            sh.text((x + 0.5, y_lo + 0.3), "to motor −  =  GND_BUS (black)", check=False)
            sh.sax.annotate("BAND = cathode (K)", xy=(x + 0.75, bar), xytext=(x + 2.2, bar + 1.2), fontsize=FS,
                            fontweight="bold", color=WARN_C, va="center", arrowprops=lead)
            sh.sax.annotate("plain end = anode (A)", xy=(x + 0.6, mid - 0.6), xytext=(x + 2.2, mid - 1.6),
                            fontsize=FS, va="center", arrowprops=lead)
        else:
            sh.text((x, y_hi + 0.55), "✗ WRONG (reversed)", ha="center", weight="bold", color=WARN_C, check=False)
            sh.sax.plot([x - 1.0, x + 1.0], [mid - 1.0, mid + 1.0], color=WARN_C, lw=2.2, zorder=7)
            sh.sax.plot([x - 1.0, x + 1.0], [mid + 1.0, mid - 1.0], color=WARN_C, lw=2.2, zorder=7)
            sh.text((x + 1.5, mid + 0.4), "Band on motor − : the diode", color=WARN_C, check=False)
            sh.text((x + 1.5, mid - 0.2), "shorts +12 V the moment the", color=WARN_C, check=False)
            sh.text((x + 1.5, mid - 0.8), "relay closes → F1 blows.", color=WARN_C, check=False)
    sh.legend(0.3, 6.3)
    sh.finish()
    return sh


# ----------------------------------------------------------------------------
# Sheet 4: ESP32 and sensors
# ----------------------------------------------------------------------------
def sheet_esp32():
    sh = Sheet(4, "ESP32 and ultrasonic sensors", "SHEET 4 — ESP32 AND SENSORS   (§2.3, §7, §8)")
    tops = [33.4, 26.0, 18.6, 11.2]       # VCC row of each sensor
    u_top, u_bot = 36.6, 8.6
    ux, uw = 9.0, 9.4
    rpins = [("GPIO26", "R", u_top - 36.0, "GPIO26"), ("GPIO27", "R", u_top - 35.1, "GPIO27"),
             ("GPIO25", "R", u_top - 34.2, "GPIO25")]
    for (n, pos, trig, echo), yt in zip(SENSORS, tops):
        lab_e = {"GPIO36": "GPIO36 (VP)", "GPIO39": "GPIO39 (VN)"}.get(echo, echo)
        rpins.append((trig, "R", u_top - (yt - 0.9), trig))
        rpins.append((echo, "R", u_top - (yt - 1.8), lab_e))
    lpins = [("VIN", "L", u_top - 36.0, "VIN (5V)"), ("GND", "L", u_top - 35.1, "GND"),
             ("3V3", "L", u_top - 33.3, "3V3"), ("GPIO2", "L", u_top - 31.5, "GPIO2")]
    u1 = Block(uw, u_top - u_bot, lpins + rpins,
               center="**U1\nESP32 DevKit\n(ESP-WROOM-32)\n3.3 V logic\n\nonly the pins used\nare shown; find them\n"
                      "by the silkscreen\nlabel (GPIOn / Dn)")
    sh.part("U1", u1.at((ux, u_bot)), {p[0]: p[0] for p in lpins + rpins})

    # left side: power and unused pins
    vx, vy = sh.pin("U1", "VIN")
    sh.flag((vx - 1.0, vy), "+5V · J1 · Sheet 2", [("J1", "2")], direction="left", color=WIRE["orange"])
    sh.wire(find("J1.2", "U1.VIN"), [(vx - 1.0, vy), (vx, vy)])
    gx, gy = sh.pin("U1", "GND")
    sh.gnd((gx - 1.4, gy))
    sh.wire(find("U1.GND", "TB2.GND"), [(gx, gy), (gx - 1.4, gy)], g=(0, "above", 0.5), gtext="22 AWG")
    sh.nc("U1", "3V3", label="3V3: do NOT feed 5 V here", side="left", color=WARN_C)
    sh.nc("U1", "GPIO2", label="onboard LED, no wiring", side="left")

    # relay outputs -> off-sheet flags
    for n, gpio, _ in CHANNELS:
        x, y = sh.pin("U1", gpio)
        fx = 21.2
        sh.flag((fx, y), f"K{n} IN · Sheet 3", [(f"K{n}", "IN")])
        sh.wire(find(f"U1.{gpio}", f"K{n}.IN"), [(x, y), (fx, y)])
    x, y = sh.pin("U1", "GPIO25")
    sh.text((19.0, 36.75), "22–24 AWG yellow, + 10 kΩ pull-down at each IN", fs=FS, color=GAUGE_C, style="italic")

    # sensors with ECHO dividers
    for (n, pos, trig, echo), yt in zip(SENSORS, tops):
        Sx, Rt, Rb = f"S{n}", f"R{n + 3}", f"R{n + 7}"
        sb = Block(5.6, 3.6, [("VCC", "L", 0.45, None), ("TRIG", "L", 1.35, None), ("ECHO", "L", 2.25, None),
                              ("GND", "L", 3.15, None)], center=None)
        sh.part(Sx, sb.at((40.4, yt - 3.15)), {p: p for p in ("VCC", "TRIG", "ECHO", "GND")})
        sh.text((44.35, yt - 0.85), f"{Sx}", ha="center", weight="bold", fs=9, check=False)
        sh.text((44.35, yt - 1.5), "HC-SR04", ha="center", check=False)
        sh.text((44.35, yt - 2.15), pos, ha="center", check=False)
        # VCC and GND
        vx, vy = sh.pin(Sx, "VCC")
        sh.vdd((vx - 1.6, vy), "+5V", ("J1", "2"))
        sh.wire(find("J1.2", f"{Sx}.VCC"), [(vx - 1.6, vy), (vx, vy)])
        gx, gy = sh.pin(Sx, "GND")
        sh.gnd((gx - 1.6, gy))
        sh.wire(find(f"{Sx}.GND", "TB2.GND"), [(gx, gy), (gx - 1.6, gy)])
        # TRIG direct
        tx, ty = sh.pin("U1", trig)
        sh.wire(find(f"U1.{trig}", f"{Sx}.TRIG"), [(tx, ty), sh.pin(Sx, "TRIG")])
        sh.text((23.0, ty + 0.3), f"TRIG {trig} → {Sx}  (direct)", ha="center", fs=FS, color=WIRE["green"])
        # ECHO divider
        ex, ey = sh.pin(Sx, "ECHO")
        r1x0, r1x1 = 34.2, 31.6
        rt = elm.Resistor().endpoints((r1x0, ey), (r1x1, ey)).color(PART_C)
        sh.part(Rt, rt, {"1": "start", "2": "end"})
        sh.text(((r1x0 + r1x1) / 2, ey - 0.45), f"{Rt}  1 kΩ", ha="center", va="top")
        nx = 27.6
        rb = elm.Resistor().endpoints((nx, ey), (nx, ey - 2.2)).color(PART_C)
        sh.part(Rb, rb, {"1": "start", "2": "end"})
        sh.text((nx + 0.4, ey - 1.1), f"{Rb}\n2 kΩ", ha="left")
        gpx, gpy = sh.pin("U1", echo)
        sh.wire(find(f"{Sx}.ECHO", f"{Rt}.1"), [(ex, ey), sh.pin(Rt, "1")])
        sh.wire(find(f"{Rt}.2", f"U1.{echo}"), [sh.pin(Rt, "2"), (nx, ey), (gpx, gpy)])
        sh.wire(find(f"U1.{echo}", f"{Rb}.1"), [sh.pin(Rb, "1"), (gpx, gpy)])
        sh.gnd((nx, ey - 2.7))
        sh.wire(find(f"{Rb}.2", "TB2.GND"), [sh.pin(Rb, "2"), (nx, ey - 2.7)])
        sh.text((37.0, ey + 0.3), "0 / 5 V", ha="center", fs=FS, color=WIRE["blue"])
        sh.text((23.0, ey - 0.35), f"ECHO {echo} ← {Sx}  (0 / 3.33 V)", ha="center", fs=FS, color=WIRE["blue"])

    ytop = sh.note(46.8, 37.3, 8.8, [
        ("•", "TRIG goes direct: 3.3 V is enough for TRIG."),
        ("•", "ECHO outputs 5 V; ESP32 pins tolerate 3.3 V only — always through the divider."),
        ("•", "GPIO34–39 are input-only and have no internal pull resistors; the 2 kΩ is also their "
              "pull-down."),
        ("•", "Solder the dividers on a small perfboard next to the ESP32 (long wires carry 5 V, short "
              "ones 3.3 V)."),
        ("•", "GPIO25/26/27 stay LOW at boot, so relays cannot click on during start-up."),
        ("•", "VIN may be printed “5V”; GPIO36/39 may be printed VP/VN."),
        ("•", "JSN-SR04T (waterproof) wires the same way, same divider (§10)."),
    ], title="Notes (§2.3, §8)")
    sh.note(46.8, ytop - 0.5, 8.8, [
        "HC-SR04 ECHO (0 / 5 V) → 1 kΩ (R4–R7) → node → ESP32 GPIO; node → 2 kΩ (R8–R11) → GND.",
        "Vout = 5 V × 2 kΩ / 3 kΩ = 3.33 V",
        "Use exactly 1 kΩ and 2 kΩ (two 1 kΩ in series make 2 kΩ). 2.2 kΩ gives 3.44 V — above 3.3 V.",
    ], title="ECHO divider math (one per sensor, §8)", title_color=WIRE["blue"], edge=WIRE["blue"])
    sh.legend(0.3, 6.3)
    sh.finish()
    return sh


# ----------------------------------------------------------------------------
# Sheet 5: transistor fallback
# ----------------------------------------------------------------------------
def sheet_fallback():
    sh = Sheet(5, "Transistor fallback detail", "SHEET 5 — TRANSISTOR FALLBACK (OPTIONAL)   (§6.3, §6.4)")
    rows = [29.8, 20.0, 10.2]
    for (n, gpio, desc), yc in zip(CHANNELS, rows):
        K, Q, Rb, R = f"K{n}", f"Q{n}", f"R{11 + n}", f"R{n}"
        sh.text((0.3, yc + 2.75), f"CH{n} · {gpio} → {Q} → {K} IN", weight="bold", fs=9)
        sh.text((0.3, yc + 2.15), desc, fs=FS)
        fx = 4.9
        sh.flag((fx, yc), f"U1 {gpio} · Sheet 4", [("U1", gpio)], direction="left")
        nx = 6.0
        r10 = elm.Resistor().endpoints((nx, yc), (nx, yc - 2.2)).color(PART_C)
        sh.part(R, r10, {"1": "start", "2": "end"})
        sh.text((nx - 0.35, yc - 1.1), f"{R}  10 kΩ\n(moved from\nIN to here)", ha="right")
        sh.gnd((nx, yc - 2.7))
        sh.wire(find(f"{R}.2", "TB2.GND", "fallback"), [sh.pin(R, "2"), (nx, yc - 2.7)])
        rb = elm.Resistor().endpoints((7.2, yc), (9.8, yc)).color(PART_C)
        sh.part(Rb, rb, {"1": "start", "2": "end"})
        sh.text((8.5, yc + 0.45), f"{Rb}  1 kΩ", ha="center", va="bottom")
        q = elm.BjtNpn(circle=True).right().at((10.6, yc)).anchor("base").color(PART_C)
        sh.part(Q, q, {"B": "base", "C": "collector", "E": "emitter"})
        sh.text((12.0, yc - 0.35), f"{Q}\n2N2222 /\nBC547", ha="left", va="top")
        sh.wire(find(f"U1.{gpio}", f"{Rb}.1", "fallback"), [(fx, yc), sh.pin(Rb, "1")])
        sh.wire(find(f"U1.{gpio}", f"{R}.1", "fallback"), [(fx, yc), sh.pin(R, "1")])
        sh.wire(find(f"{Rb}.2", f"{Q}.B", "fallback"), [sh.pin(Rb, "2"), sh.pin(Q, "B")])
        ex, ey = sh.pin(Q, "E")
        sh.gnd((ex, yc - 2.7))
        sh.wire(find(f"{Q}.E", "TB2.GND", "fallback"), [(ex, ey), (ex, yc - 2.7)])
        k = Block(5.8, 3.4, [("IN", "L", 1.7, None), ("DC+", "T", 0.8, None), ("DC-", "B", 0.8, None)],
                  center=f"**{K}\nFC65 relay\nJumper: L\nload side as Sheet 3")
        sh.part(K, k.at((14.6, yc - 0.2)), {"IN": "IN", "DC+": "DC+", "DC-": "DC-"})
        ix, iy = sh.pin(K, "IN")
        cx, cy = sh.pin(Q, "C")
        sh.wire(find(f"{Q}.C", f"{K}.IN", "fallback"), [(cx, cy), (cx, iy), (ix, iy)])
        px, py = sh.pin(K, "DC+")
        sh.vdd((px, py + 0.55), "+12V_BUS", ("TB1", "+"))
        sh.wire(find("TB1.+", f"{K}.DC+"), [(px, py + 0.55), (px, py)], g=(0, "left", 0.5))
        gx, gy = sh.pin(K, "DC-")
        sh.gnd((gx, gy - 0.55))
        sh.wire(find("TB2.GND", f"{K}.DC-"), [(gx, gy), (gx, gy - 0.55)], g=(0, "right", 0.5))

    # right side: logic chain, pinouts and notes
    x0, w = 23.4, 32.0
    ytop = sh.note(x0, 37.3, w, [
        "Use only for a module that fails the 3.3 V trigger test of §6.3 (does not click every time, chatters, "
        "or IN current < 0.5 mA). Each channel is converted on its own.",
        ("1.", "Set THIS module's trigger jumper to L (LOW-level trigger)."),
        ("2.", "Remove the direct GPIO → IN wire and move the 10 kΩ (R1/R2/R3) from IN to the GPIO side as "
               "drawn. Interpretation: §6.4 does not say this explicitly, but with the jumper on L a 10 kΩ left from IN to GND "
               "can hold the relay ON."),
        ("3.", "Logic is unchanged, the firmware needs no change: GPIO HIGH → transistor ON → IN pulled LOW → "
               "relay ON. No GPIO signal → transistor OFF → IN floats → relay OFF."),
        ("4.", "The ESP32 pin only sees the 1 kΩ base resistor, never 12 V."),
    ], title="When and how to use the fallback (§6.3, §6.4)")
    ytop = sh.note(x0, ytop - 0.4, 15.4, [
        "2N2222 (TO-92, flat side facing you, legs down): E  B  C",
        "BC547 (same view): C  B  E",
        "Pinouts differ between parts and makers — check your datasheet.",
    ], title="Transistor pinouts (§6.4)")
    # TO-92 drawings
    for i, (name, legs) in enumerate((("2N2222", "E B C"), ("BC547", "C B E"))):
        cx = 41.6 + i * 7.2
        cy = 28.6
        body = patches.FancyBboxPatch((cx - 1.4, cy - 0.2), 2.8, 2.6, boxstyle="round,pad=0,rounding_size=0.9",
                                      facecolor="#e9e9e4", edgecolor=PART_C, lw=1.2)
        sh.sax.add_patch(body)
        sh.text((cx, cy + 1.2), name, ha="center", weight="bold", check=False)
        sh.text((cx, cy + 0.45), "flat face", ha="center", check=False)
        for j, leg in enumerate(legs.split()):
            lx = cx - 0.9 + j * 0.9
            sh.sax.plot([lx, lx], [cy - 0.2, cy - 1.6], color=PART_C, lw=1.6)
            sh.text((lx, cy - 2.0), leg, ha="center", weight="bold", fs=9, check=False)
    sh.text((45.2, 24.95), "TO-92, viewed from the flat side, legs down", ha="center", check=False)
    sh.legend(0.3, 6.3)
    sh.finish()
    return sh


# ----------------------------------------------------------------------------
# Sheet 1: system overview (block diagram, mm coordinates on the page axes)
# ----------------------------------------------------------------------------
class Overview:
    def __init__(self, sh):
        self.sh, self.ax = sh, sh.pax
        self.blocks = {}
        self.errors = []
        self.used = set()

    def box(self, key, x, y, w, h, title, lines=(), parts=(), edge=PART_C, stack=0, fill=BODY_FILL, fs=FS):
        for k in range(stack, 0, -1):
            self.ax.add_patch(patches.FancyBboxPatch((x + 1.6 * k, y - 1.6 * k), w, h,
                                                     boxstyle="round,pad=0,rounding_size=2", facecolor="white",
                                                     edgecolor=edge, lw=1.0, zorder=2))
        self.ax.add_patch(patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=2",
                                                 facecolor=fill, edgecolor=edge, lw=1.5, zorder=3))
        n = 1 + len(lines)
        lh = 4.1
        y0 = y + h / 2 + (n - 1) * lh / 2
        self.ax.text(x + w / 2, y0, title, ha="center", va="center", fontsize=9, fontweight="bold", zorder=4)
        for i, ln in enumerate(lines):
            self.ax.text(x + w / 2, y0 - (i + 1) * lh, ln, ha="center", va="center", fontsize=fs, zorder=4,
                         color=WARN_C if ln.startswith("⚠") else NOTE_C)
        self.blocks[key] = set(parts)

    def link(self, pts, colour, cids, a, b, label=None, lpos=None, arrow=True, ha="center", lw=2.0, hops=()):
        for cid in cids:
            c = CONN.get(cid)
            if c is None:
                self.errors.append(f"overview link {a}->{b}: unknown id {cid}")
                continue
            ends = {c.a[0], c.b[0]} if c.b else {c.a[0]}
            if not (ends <= self.blocks[a] | self.blocks[b]) or not (ends & self.blocks[a]) or \
                    not (ends & self.blocks[b]):
                self.errors.append(f"overview link {a}->{b}: {cid} joins {ends}")
            self.used.add(cid)
        xs, ys = zip(*pts)
        col = WIRE[colour]
        if colour == "yellow":
            self.ax.plot(xs, ys, color=YELLOW_EDGE, lw=lw + 1.2, solid_capstyle="butt", zorder=4.4)
        self.ax.plot(xs, ys, color=col, lw=lw, solid_capstyle="butt", solid_joinstyle="miter", zorder=4.5)
        for hx, hy in hops:      # white gap where this line crosses another one
            self.ax.add_patch(patches.Rectangle((hx - 2.2, hy - 2.2), 4.4, 4.4, facecolor="white",
                                                edgecolor="none", zorder=4.6))
            self.ax.add_patch(patches.Arc((hx, hy), 4.4, 4.4, theta1=0, theta2=180, color=col, lw=lw,
                                          zorder=4.7))
        if arrow:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            self.ax.annotate("", xy=(x2, y2), xytext=(x1 + (x2 - x1) * 0.6, y1 + (y2 - y1) * 0.6),
                             arrowprops=dict(arrowstyle="-|>,head_length=0.7,head_width=0.35", color=col, lw=lw,
                                             shrinkA=0, shrinkB=0), zorder=4.8)
        if label:
            lx, ly = lpos
            self.ax.text(lx, ly, label, ha=ha, va="center", fontsize=FS, color=NOTE_C, zorder=5,
                         bbox=dict(facecolor="white", edgecolor="none", pad=0.6))

    def gnd(self, x, y):
        ax = self.ax
        ax.plot([x, x], [y, y - 3.0], color=PART_C, lw=1.3, zorder=4)
        for dx, dy in ((2.4, 3.0), (1.6, 4.0), (0.8, 5.0)):
            ax.plot([x - dx, x + dx], [y - dy, y - dy], color=PART_C, lw=1.3, zorder=4)


def sheet_overview():
    sh = Sheet(1, "System overview", "SHEET 1 — SYSTEM OVERVIEW   (§2.1 power path, §2.2 signal path)")
    ov = Overview(sh)
    B = ov.box
    B("PV1", 25, 222, 82, 34, "PV1  Solar panel", ["12 V 10 W polycrystalline", "Vmp ≈ 18 V, Imp ≈ 0.55 A"], ["PV1"])
    B("BT1", 25, 170, 82, 40, "BT1  LiFePO4 battery", ["12.8 V 15 Ah 4S1P", "IFR 32140FS, built-in BMS"], ["BT1"])
    B("F1", 120, 178, 36, 22, "F1  15 A fuse", ["≤ 15 cm from BT1 +"], ["F1"], edge=WARN_C)
    B("U3", 172, 168, 84, 92, "U3  Charge controller", ["30 A PWM, LCD", "mode LI / USER", "LOAD = always ON",
                                                          "LVD 12.0 V on LOAD"], ["U3"])
    B("SW1", 274, 180, 36, 20, "SW1  ON/OFF", ["≥ 15 A DC"], ["SW1"])
    B("TB2", 326, 218, 72, 40, "TB2  GND_BUS", ["star point (bus bar)", "every ground symbol below", "returns here"],
      ["TB2"], edge=PART_C)
    B("TB1", 326, 172, 72, 30, "TB1  +12V_BUS", ["terminal block"], ["TB1"], edge=WIRE["red"])
    B("U2", 186, 110, 46, 30, "U2  UBEC", ["12 V → 5 V / 3 A"], ["U2"])
    B("J1", 146, 116, 26, 20, "J1  link", ["unplug for USB"], ["J1"])
    B("U1", 96, 88, 40, 58, "U1  ESP32", ["DevKit", "ESP-WROOM-32", "3.3 V logic"], ["U1"])
    B("DIV", 63, 92, 27, 26, "Dividers", ["R4–R7 1 kΩ", "R8–R11 2 kΩ"], ["R4", "R5", "R6", "R7", "R8", "R9",
                                                                         "R10", "R11"])
    B("S", 22, 98, 36, 46, "S1–S4", ["HC-SR04 ×4", "FL, FR, BL, BR"], ["S1", "S2", "S3", "S4"], stack=2)
    B("K", 280, 104, 50, 40, "K1–K3  FC65", ["relay modules ×3", "jumper H", "+ R1–R3 10 kΩ at IN"],
      ["K1", "K2", "K3", "R1", "R2", "R3"], stack=2)
    B("M", 350, 104, 48, 40, "M1–M3  motors", ["left, right, conveyor", "+ D1–D3, C1–C3", "at the terminals"],
      ["M1", "M2", "M3", "D1", "D2", "D3", "C1", "C2", "C3"], stack=2)

    L = ov.link
    L([(107, 245), (172, 245)], "red", [find("PV1.+", "U3.PV+")], "PV1", "U3", "PV+  18 AWG", (139, 245))
    L([(107, 233), (172, 233)], "black", [find("PV1.-", "U3.PV-")], "PV1", "U3", "PV−  18 AWG", (139, 233))
    L([(107, 196), (120, 196)], "red", [find("BT1.+", "F1.1")], "BT1", "F1")
    L([(156, 189), (172, 189)], "red", [find("F1.2", "U3.BAT+")], "F1", "U3", "BAT+", (164, 193.5))
    L([(107, 176), (172, 176)], "black", [find("BT1.-", "U3.BAT-")], "BT1", "U3", "BAT−  14 AWG", (139, 176))
    L([(256, 240), (326, 240)], "black", [find("U3.LOAD-", "TB2.GND")], "U3", "TB2", "LOAD−  14 AWG", (291, 240))
    L([(256, 190), (274, 190)], "red", [find("U3.LOAD+", "SW1.1")], "U3", "SW1", arrow=False)
    sh.pax.text(265, 194.5, "LOAD+", ha="center", va="center", fontsize=FS, zorder=5)
    L([(310, 190), (326, 190)], "red", [find("SW1.2", "TB1.+")], "SW1", "TB1")
    sh.pax.text(318, 194.5, "14 AWG", ha="center", va="center", fontsize=FS, color=GAUGE_C, style="italic")
    sh.pax.text(292, 211, "⚠ SW1 is on the LOAD+ line,\nnot the battery line (§4)", ha="center", va="center",
                fontsize=FS, color=WARN_C, fontweight="bold", linespacing=1.2)
    sh.pax.text(25, 163, "⚠ NO direct wire from BT1 − to GND_BUS:\nbattery − reaches ground only through U3 (§9)",
                ha="left", va="center", fontsize=FS, color=WARN_C, fontweight="bold", linespacing=1.2)
    # 12 V distribution
    L([(350, 172), (350, 162), (305, 162), (305, 144)], "red",
      [find("TB1.+", f"K{n}.DC+") for n in (1, 2, 3)] + [find("TB1.+", f"K{n}.COM") for n in (1, 2, 3)],
      "TB1", "K", "DC+ and COM", (318, 157), ha="left")
    L([(340, 172), (340, 166), (209, 166), (209, 140)], "red", [find("TB1.+", "U2.IN+")], "TB1", "U2",
      "IN+  20–22 AWG", (240, 166))
    L([(330, 124), (350, 124)], "red", [find(f"K{n}.NO", f"M{n}.+") for n in (1, 2, 3)], "K", "M", "NO→M+",
      (341, 130))
    # 5 V
    L([(186, 126), (172, 126)], "orange", [find("U2.5V", "J1.1")], "U2", "J1")
    L([(146, 126), (136, 126)], "orange", [find("J1.2", "U1.VIN")], "J1", "U1", "VIN", (141, 130.5))
    L([(159, 136), (159, 152), (40, 152), (40, 144)], "orange", [find("J1.2", f"S{n}.VCC") for n in (1, 2, 3, 4)],
      "J1", "S", "+5V → S1–S4 VCC  (orange 22 AWG)", (100, 152))
    # signals
    L([(136, 100), (150, 100), (150, 96), (292, 96), (292, 104)], "yellow",
      [find(f"U1.{g}", f"K{n}.IN") for n, g, _ in CHANNELS], "U1", "K",
      "GPIO26 / 27 / 25 → K1 / K2 / K3 IN", (236, 96), hops=[(198, 96)])
    L([(96, 132), (58, 132)], "green", [find(f"U1.{t}", f"S{n}.TRIG") for n, _, t, _ in SENSORS], "U1", "S",
      "TRIG  GPIO16–19", (77, 136.5))
    L([(58, 105), (63, 105)], "blue", [find(f"S{n}.ECHO", f"R{n + 3}.1") for n, *_ in SENSORS], "S", "DIV",
      arrow=False)
    L([(90, 105), (96, 105)], "blue", [find(f"R{n + 3}.2", f"U1.{e}") for n, _, _, e in SENSORS], "DIV", "U1")
    sh.pax.text(78, 125, "ECHO\nGPIO34/35/36/39\nvia 1 kΩ / 2 kΩ", ha="center", va="center", fontsize=FS,
                linespacing=1.2)
    # ground glyphs on every block that returns to TB2
    for x, y in ((40, 98 - 3.2), (76, 92), (116, 88), (200, 110), (320, 104 - 3.2), (386, 104 - 3.2)):
        ov.gnd(x, y)

    sh.legend(0.3, 6.3)
    sh.finish()
    # all overview links must correspond to netlist entries
    sh.errors.extend(ov.errors)
    return sh, ov


# ----------------------------------------------------------------------------
# Sheet 6: reference tables (mm coordinates on the page axes)
# ----------------------------------------------------------------------------
def table(ax, x, ytop, widths, header, rows, title=None, fs=FS, pad=1.4, swatch_col=None):
    lh = fs * 1.25 * 25.4 / 72
    y = ytop
    if title:
        ax.text(x, y - 2.6, title, ha="left", va="center", fontsize=10, fontweight="bold")
        y -= 5.6
    allrows = [header] + rows
    for ri, row in enumerate(allrows):
        cells = []
        for ci, (val, w) in enumerate(zip(row, widths)):
            avail = w - 2 * pad - (7.0 if (swatch_col == ci and ri > 0) else 0)
            lines = []
            for part in str(val).split("\n"):
                lines += wrap(part, avail, fs, ri == 0, measure=text_w_mm)
            cells.append(lines)
        h = max(len(c) for c in cells) * lh + 2 * pad - 0.4
        cx = x
        for ci, (lines, w) in enumerate(zip(cells, widths)):
            ax.add_patch(patches.Rectangle((cx, y - h), w, h, facecolor="#e6e6e0" if ri == 0 else "white",
                                           edgecolor="#555555", lw=0.6))
            tx = cx + pad
            if swatch_col == ci and ri > 0:
                key = row[ci].split()[0].lower().split("/")[0]
                for k in ([key] if key in WIRE else []):
                    if k == "yellow":
                        ax.plot([tx, tx + 5.5], [y - pad - lh / 2] * 2, color=YELLOW_EDGE, lw=4.0)
                    ax.plot([tx, tx + 5.5], [y - pad - lh / 2] * 2, color=WIRE[k], lw=3.0)
                if row[ci].startswith("Red /"):
                    ax.plot([tx, tx + 2.6], [y - pad - lh / 2] * 2, color=WIRE["red"], lw=3.0)
                    ax.plot([tx + 2.9, tx + 5.5], [y - pad - lh / 2] * 2, color=WIRE["black"], lw=3.0)
                tx += 7.0
            for li, ln in enumerate(lines):
                ax.text(tx, y - pad - lh * (li + 0.5), ln, ha="left", va="center", fontsize=fs,
                        fontweight="bold" if ri == 0 else "normal", color=PART_C)
            cx += w
        y -= h
    return y


def sheet_reference():
    sh = Sheet(6, "Reference: pin map, wires, BOM, settings", "SHEET 6 — REFERENCE   (§1, §2.3, §3.3, safety notes)")
    ax = sh.pax
    bom = [
        ["PV1", "1", "Solar panel, polycrystalline", "12 V 10 W; Vmp ≈ 18 V, Imp ≈ 0.55 A", "§1"],
        ["U3", "1", "LCD PWM solar charge controller", "30 A; must have LI / USER mode", "§1, §3"],
        ["BT1", "1", "Pro-Range IFR 32140FS LiFePO4 pack", "12.8 V 15 Ah 4S1P; check built-in BMS/PCM", "§1, §5"],
        ["F1", "1", "Blade fuse + inline fuse holder", "15 A; on battery +, ≤ 15 cm from battery", "§1, §4"],
        ["SW1", "1", "Main ON/OFF switch", "≥ 15 A DC; on the LOAD+ line", "§1, §4"],
        ["TB1", "1", "+12 V bus terminal block", "Named in §4; not a separate line in §1", "§4"],
        ["TB2", "1", "Ground bus bar / terminal block", "One common ground, star point", "§1, §9"],
        ["U2", "1", "UBEC", "5 V / 3 A, 12 V in", "§1, §7"],
        ["J1", "1", "2-pin connector / jumper in UBEC 5 V out", "Unplug for USB programming", "§1, §7"],
        ["U1", "1", "ESP32 DevKit", "ESP-WROOM-32", "§1, §2.3"],
        ["K1–K3", "3", "FC65 optocoupler relay module", "12 V coil, 30 A contacts; jumper H", "§1, §6"],
        ["M1, M2", "2", "Rhino IG32 planetary geared motor", "12 V 100 RPM; left / right propulsion", "§1, §6"],
        ["M3", "1", "Conveyor motor", "ASSUMED 12 V DC brushed geared — verify", "§1, §6.2"],
        ["D1–D3", "3", "Flyback diode", "1N5408, or Schottky SB560 / SR560 / MBR1045", "§1, §6.2"],
        ["C1–C3", "3", "Ceramic capacitor (optional)", "0.1 µF (100 nF), ≥ 50 V", "§1, §6.2"],
        ["R1–R3", "3", "Resistor, relay IN pull-down", "10 kΩ", "§1, §6.1"],
        ["R4–R7", "4", "Resistor, ECHO divider (top)", "1 kΩ", "§1, §8"],
        ["R8–R11", "4", "Resistor, ECHO divider (bottom)", "2 kΩ — not 2.2 kΩ", "§1, §8"],
        ["S1–S4", "4", "HC-SR04 ultrasonic sensor", "5 V; or JSN-SR04T waterproof", "§1, §8, §10"],
        ["Q1–Q3", "3", "NPN transistor (fallback only)", "2N2222 or BC547", "§1, §6.4"],
        ["R12–R14", "3", "Resistor, base (fallback only)", "1 kΩ", "§1, §6.4"],
    ]
    y = table(ax, 20, 268, [19, 9, 58, 66, 22], ["Ref", "Qty", "Part", "Value / rating", "Source"], bom,
              title="Bill of materials (§1) — designators match Sheets 1–5")
    wires = [
        ["Red / Black", "Battery & main 12 V line: battery → fuse → controller, LOAD → switch → 12 V bus", "14 AWG (2.5 mm²)"],
        ["Red / Black", "Solar panel → controller", "18 AWG (0.75–1.0 mm²)"],
        ["Red / Black", "12 V bus → relay COM, relay NO → motor, motor → GND", "16–18 AWG (1.0–1.5 mm²)"],
        ["Red / Black", "12 V bus → relay module DC+/DC−, UBEC input", "20–22 AWG"],
        ["Orange", "5 V (UBEC → ESP32, sensors)", "22 AWG"],
        ["Yellow", "Relay signals (GPIO → IN)", "22–24 AWG"],
        ["Green", "Sensor TRIG", "22–24 AWG"],
        ["Blue", "Sensor ECHO", "22–24 AWG"],
        ["Black", "Logic ground (ESP32, sensors, relay modules)", "22 AWG"],
    ]
    table(ax, 20, y - 6, [31, 104, 39], ["Colour", "Wire", "Gauge"], wires,
          title="Wire colours and gauges (§1)", swatch_col=0)

    pinmap = [
        ["Left motor relay", "GPIO 26", "FC65 #1 (K1) IN + 10 kΩ (R1) to GND"],
        ["Right motor relay", "GPIO 27", "FC65 #2 (K2) IN + 10 kΩ (R2) to GND"],
        ["Conveyor relay", "GPIO 25", "FC65 #3 (K3) IN + 10 kΩ (R3) to GND"],
        ["Front-Left TRIG / ECHO", "GPIO 16 / GPIO 34", "HC-SR04 #1 (S1); ECHO via R4 / R8"],
        ["Front-Right TRIG / ECHO", "GPIO 17 / GPIO 35", "HC-SR04 #2 (S2); ECHO via R5 / R9"],
        ["Back-Left TRIG / ECHO", "GPIO 18 / GPIO 36 (VP)", "HC-SR04 #3 (S3); ECHO via R6 / R10"],
        ["Back-Right TRIG / ECHO", "GPIO 19 / GPIO 39 (VN)", "HC-SR04 #4 (S4); ECHO via R7 / R11"],
        ["Status LED", "GPIO 2", "Onboard LED (no wiring)"],
        ["5 V in", "VIN (sometimes 5V)", "UBEC 5 V out via J1"],
        ["Ground", "GND", "Ground bus (TB2)"],
    ]
    x2 = 214
    y2 = table(ax, x2, 268, [44, 44, 98], ["Function", "ESP32 pin", "Goes to"], pinmap, title="Final pin map (§2.3)")
    settings = [
        ["Battery type", "LI / USER  (never SLD, GEL or FLD)"],
        ["System voltage", "12 V"],
        ["Charge (boost/bulk) voltage", "14.4 V"],
        ["Float voltage", "13.6 V"],
        ["Equalisation", "OFF (or = 14.4 V if it cannot be disabled)"],
        ["Low-voltage disconnect (LVD)", "12.0 V"],
        ["Low-voltage reconnect (LVR)", "12.8 V"],
        ["Temperature compensation", "OFF / 0 mV/°C"],
        ["LOAD output mode", "Always ON (often “24H” or “Normal”); confirm LOAD+ to LOAD− reads battery voltage in daylight"],
    ]
    y3 = table(ax, x2, y2 - 6, [64, 122], ["Setting", "Value"], settings,
               title="U3 charge controller settings for LiFePO4 (§3.3)")
    safety = [
        ("1.", "Fit F1 (15 A) FIRST, within 15 cm of the battery +. Keep the battery disconnected until the end."),
        ("2.", "First power-up with the motors DISCONNECTED (README “Bench test”). Check polarity: red +, black −."),
        ("3.", "Before connecting any FC65 IN to the ESP32: module on 12 V, jumper on H, nothing on IN — IN to "
               "DC− must read ≈ 0 V. 5–12 V would destroy the ESP32 pin."),
        ("4.", "Never feed 5 V into the ESP32 3V3 pin; 5 V goes to VIN only. Check U2 output is 4.9–5.2 V first."),
        ("5.", "USB programming: SW1 OFF → unplug the J1 5 V link → plug USB and upload → unplug USB → refit J1 → "
               "SW1 ON. For Serial debugging with motors powered keep J1 unplugged and run the ESP32 from USB."),
        ("6.", "No wire from battery − to the GND bus; all loads on LOAD+/LOAD− only. Connect battery → panel → "
               "loads; disconnect in reverse."),
        ("7.", "Never use relay NC. Diode band on motor + / NO wire — reversed, it shorts 12 V and blows F1."),
        ("8.", "Switch SW1 OFF when the boat is not in use: idle drain (~3 Ah/day) exceeds the panel's ~2.5 Ah/day."),
    ]
    # safety box (mm) placed in the remaining right-column space above the title block
    sy = y3 - 6
    ax.text(x2, sy - 2.6, "Key safety notes", ha="left", va="center", fontsize=10, fontweight="bold", color=WARN_C)
    sy -= 6.2
    lh = FS * 1.25 * 25.4 / 72
    box_top = sy + 1.2
    rows_ = []
    for pre, body in safety:
        lines = wrap(body, 186 - 12, FS, measure=text_w_mm)
        rows_.append((pre, lines))
    total = sum(len(l) for _, l in rows_) * lh + 2.4
    ax.add_patch(patches.Rectangle((x2, box_top - total), 186, total, facecolor="#fff6f6", edgecolor=WARN_C, lw=1.0))
    yy = box_top - 1.2 - lh / 2
    for pre, lines in rows_:
        ax.text(x2 + 2, yy, pre, ha="left", va="center", fontsize=FS, fontweight="bold")
        for ln in lines:
            ax.text(x2 + 8, yy, ln, ha="left", va="center", fontsize=FS)
            yy -= lh
    sh.ref_bottom = box_top - total
    sh.finish()
    return sh


# ----------------------------------------------------------------------------
# Cross-check and output
# ----------------------------------------------------------------------------
def net_groups(conns):
    """Electrical nets (sets of part pins) formed by a list of connections."""
    parent = {}

    def f(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for c in conns:
        if c.b:
            parent[f(c.a)] = f(c.b)
    out = defaultdict(set)
    for x in list(parent):
        out[f(x)].add(x)
    return sorted(sorted(g) for g in out.values())


def crosscheck(sheets, ov):
    print("=" * 78)
    print("NETLIST CROSS-CHECK")
    print("=" * 78)
    errors = []
    drawn = set().union(*(s.drawn for s in sheets))
    all_ids = {c.id for c in NETLIST}
    missing = sorted(all_ids - drawn)
    extra = sorted(drawn - all_ids)
    print(f"netlist entries: {len(NETLIST)}  (main {sum(c.variant == 'main' for c in NETLIST)}, "
          f"fallback {sum(c.variant == 'fallback' for c in NETLIST)})")
    print(f"entries drawn on schematic sheets 2-5: {len(drawn & all_ids)}")
    print(f"block-diagram links on sheet 1 reference {len(ov.used)} entries (all checked against block membership)")
    if missing:
        errors += [f"netlist entry not drawn: {m} {CONN[m].a}->{CONN[m].b}" for m in missing]
    if extra:
        errors += [f"drawn wire not in netlist: {e}" for e in extra]
    print(f"undrawn entries: {len(missing)}   wires not from the netlist: {len(extra)}")
    # every wire path came from Sheet.wire(), which requires a netlist id; confirm endpoints were checked
    n_wires = sum(len(s.wires) for s in sheets)
    print(f"wire paths drawn: {n_wires}; each verified to start and end on the pins its entry names")
    # parts
    placed = defaultdict(set)
    for s in sheets:
        for p in s.parts:
            placed[p].add(s.num)
    for ref, sheet in PART_SHEET.items():
        if sheet not in placed.get(ref, set()):
            errors.append(f"{ref} expected on sheet {sheet}, drawn on {sorted(placed.get(ref, []))}")
    unknown = set(placed) - set(PARTS)
    if unknown:
        errors.append(f"symbols without a BOM designator: {sorted(unknown)}")
    used_parts = {c.a[0] for c in NETLIST} | {c.b[0] for c in NETLIST if c.b}
    print(f"parts in netlist: {len(used_parts)}; all placed with designators: "
          f"{'yes' if used_parts <= set(placed) else 'NO'}")
    # connectivity: rebuild nets from the drawn wires (main variant) and compare with the netlist
    groups = net_groups
    main = [c for c in NETLIST if c.variant == "main"]
    main_drawn = [c for c in main if c.id in drawn]
    same = groups(main) == groups(main_drawn)
    print(f"electrical nets rebuilt from the drawing (main build): {len(groups(main_drawn))}; "
          f"identical to netlist: {'yes' if same else 'NO'}")
    if not same:
        errors.append("connectivity of the drawing differs from the netlist")
    for s in sheets:
        for e in s.errors:
            errors.append(f"sheet {s.num}: {e}")
    print("-" * 78)
    for s in sheets:
        print(f"sheet {s.num}: {len(s.wires):3d} wire paths, {len(s.drawn):3d} entries, "
              f"{len(s.errors)} errors, {len(s.warnings)} layout warnings")
        for w in s.warnings:
            print(f"    warning: {w}")
    print("-" * 78)
    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        for e in errors:
            print("  ERROR:", e)
    else:
        print("PASSED: every netlist entry is drawn, and every drawn wire is a netlist entry.")
    return errors

# ----------------------------------------------------------------------------
# One-page simplified diagram (main build only; power/ground symbols replace the bus bars)
# ----------------------------------------------------------------------------
def legend_strip(sh, x, ytop, w):
    """Compact colour + gauge legend for the one-page diagram."""
    lh = line_h(FS)
    h = 3 * lh + 0.55
    sh.sax.add_patch(patches.Rectangle((x, ytop - h), w, h, facecolor="white", edgecolor="#777777", lw=0.8,
                                       zorder=1))
    y = ytop - 0.3 - lh * 0.4
    cx = x + 0.3
    sh.text((cx, y), "Wires:", weight="bold", check=False)
    cx += text_w("Wires:", FS, True) + 0.4
    for key, label in (("red", "+12 V"), ("black", "GND / −"), ("orange", "+5 V"), ("yellow", "relay signal"),
                       ("green", "TRIG"), ("blue", "ECHO")):
        if key == "yellow":
            sh.sax.plot([cx, cx + 1.4], [y, y], color=YELLOW_EDGE, lw=3.2, solid_capstyle="round", zorder=2)
        sh.sax.plot([cx, cx + 1.4], [y, y], color=WIRE[key], lw=2.2, solid_capstyle="round", zorder=3)
        sh.text((cx + 1.7, y), label, check=False)
        cx += 1.7 + text_w(label) + 0.8
    gx = cx + 0.4
    sh.sax.plot([gx, gx], [y + 0.25, y], color=PART_C, lw=1.1)
    for dx, dy in ((0.25, 0), (0.16, -0.1), (0.07, -0.2)):
        sh.sax.plot([gx - dx, gx + dx], [y + dy, y + dy], color=PART_C, lw=1.1)
    sh.text((gx + 0.5, y), "= ground bus TB2", check=False)
    y -= lh
    sh.text((x + 0.3, y), "Gauges: battery / main 12 V line 14 AWG · panel 18 AWG · COM, NO, motor 16–18 AWG · "
            "module DC± and UBEC input 20–22 AWG", check=False)
    y -= lh
    sh.text((x + 0.3, y), "5 V and logic ground 22 AWG · GPIO, TRIG, ECHO 22–24 AWG · ● = joined, "
            "no dot = not joined", check=False)
    sh.keepout.append((x, ytop - h, x + w, ytop))


def sheet_simple():
    set_scale(5.5)
    sh = Sheet(1, "Complete circuit, one page (simplified)",
               "SOLAR BASED WATER BOAT — COMPLETE CIRCUIT (simplified, one page)", of=1, record=False)
    V12, V5 = ("TB1", "+"), ("J1", "2")

    # ---- solar panel, battery, fuse, charge controller, main switch
    u3 = Block(7.0, 14.5, [("PV+", "L", 0.6, None), ("PV-", "L", 1.6, None), ("BAT+", "L", 7.0, None),
                           ("BAT-", "L", 8.0, None), ("LOAD+", "R", 3.0, None), ("LOAD-", "R", 10.0, None)],
               center="**U3\nPWM solar\ncharge ctrl\n30 A\n\nLI / USER\nLOAD mode:\nalways ON")
    sh.part("U3", u3.at((6.2, 31.0)), {p: p for p in ("PV+", "PV-", "BAT+", "BAT-", "LOAD+", "LOAD-")})
    pv = elm.Solar().endpoints((1.5, 42.0), (1.5, sh.pin("U3", "PV+")[1])).color(PART_C)
    sh.part("PV1", pv, {"-": "start", "+": "end"})
    sh.text((2.0, 44.5), "+", fs=9, weight="bold")
    sh.text((2.0, 42.4), "−", fs=9, weight="bold")
    sh.text((0.2, 45.9), "PV1  solar panel 12 V 10 W", weight="bold")
    sh.wire(find("PV1.+", "U3.PV+"), [sh.pin("PV1", "+"), sh.pin("U3", "PV+")])
    sh.wire(find("PV1.-", "U3.PV-"), [sh.pin("PV1", "-"), (4.6, 42.0), (4.6, sh.pin("U3", "PV-")[1]),
                                      sh.pin("U3", "PV-")])
    yb = sh.pin("U3", "BAT+")[1]
    bt = elm.Battery().endpoints((1.5, 37.2), (1.5, 34.8)).scale(1.5).color(PART_C)
    sh.part("BT1", bt, {"+": "start", "-": "end"})
    sh.text((0.75, 36.9), "+", fs=9, weight="bold", ha="right")
    sh.text((0.75, 35.1), "−", fs=9, weight="bold", ha="right")
    sh.text((0.2, 32.6), "BT1  LiFePO4\n12.8 V 15 Ah", va="center", weight="bold")
    f1 = elm.Fuse().endpoints((2.4, yb), (4.6, yb)).color(PART_C)
    sh.part("F1", f1, {"1": "start", "2": "end"})
    sh.text((0.2, 39.95), "F1 15 A fuse,\n≤ 15 cm from BT1 +", color=WARN_C, weight="bold", va="center")
    sh.wire(find("BT1.+", "F1.1"), [sh.pin("BT1", "+"), (1.5, yb), sh.pin("F1", "1")])
    sh.wire(find("F1.2", "U3.BAT+"), [sh.pin("F1", "2"), sh.pin("U3", "BAT+")])
    sh.wire(find("BT1.-", "U3.BAT-"), [sh.pin("BT1", "-"), (1.5, 33.8), (4.8, 33.8), (4.8, sh.pin("U3", "BAT-")[1]),
                                       sh.pin("U3", "BAT-")])
    sw = elm.Switch().endpoints((14.6, 42.5), (16.8, 42.5)).color(PART_C)
    sh.part("SW1", sw, {"1": "start", "2": "end"})
    sh.text((15.7, 41.75), "SW1 ON/OFF", ha="center", weight="bold")
    sh.wire(find("U3.LOAD+", "SW1.1"), [sh.pin("U3", "LOAD+"), sh.pin("SW1", "1")])
    sh.vdd((18.3, 42.5), "+12V_BUS", V12)
    sh.wire(find("SW1.2", "TB1.+"), [sh.pin("SW1", "2"), (18.3, 42.5)])
    lx, ly = sh.pin("U3", "LOAD-")
    sh.gnd((lx + 1.2, ly))
    sh.wire(find("U3.LOAD-", "TB2.GND"), [(lx, ly), (lx + 1.2, ly)])

    # ---- UBEC and the 5 V link
    u2 = Block(4.0, 2.8, [("IN+", "L", 0.7, None), ("IN-", "L", 2.1, None), ("5V", "R", 0.7, "5V"),
                          ("GND", "B", 2.0, "GND")])
    sh.part("U2", u2.at((7.0, 26.8)), {"IN+": "IN+", "IN-": "IN-", "5V": "5V", "GND": "GND"})
    sh.text((7.0, 30.15), "U2  UBEC 5 V / 3 A", weight="bold")
    ix, iy = sh.pin("U2", "IN+")
    sh.vdd((5.0, iy), "+12V_BUS", V12)
    sh.wire(find("TB1.+", "U2.IN+"), [(ix, iy), (5.0, iy)])
    nx_, ny_ = sh.pin("U2", "IN-")
    sh.gnd((4.0, ny_))
    sh.wire(find("TB2.GND", "U2.IN-"), [(nx_, ny_), (4.0, ny_)])
    gx, gy = sh.pin("U2", "GND")
    sh.gnd((gx, gy - 0.5))
    sh.wire(find("U2.GND", "TB2.GND"), [(gx, gy), (gx, gy - 0.5)])
    ox, oy = sh.pin("U2", "5V")
    sh.part("J1", Link(2.0).at((ox + 0.6, oy)), {"1": "1", "2": "2"})
    sh.wire(find("U2.5V", "J1.1"), [(ox, oy), sh.pin("J1", "1")])
    sh.text((11.9, 27.75), "J1 5 V link\n(unplug for USB)", weight="bold", va="center")
    # this +5V symbol stands for the far ends of J1's output wires (U1 VIN and S1–S4 VCC)
    e = sh.vdd((15.3, oy), "+5V", ("U1", "VIN"))
    for n in (1, 2, 3, 4):
        sh.term[P((15.3, oy))].add((f"S{n}", "VCC"))
    sh.wire([find("J1.2", "U1.VIN")] + [find("J1.2", f"S{n}.VCC") for n in (1, 2, 3, 4)],
            [sh.pin("J1", "2"), (15.3, oy)])

    # ---- ESP32
    tops = [24.6, 19.2, 13.8, 8.4]          # VCC row of each sensor
    rows = [42.6, 31.6, 20.6]               # relay channel rows
    u_top, u_bot, ux = 46.2, 4.6, 22.4
    lp = [("VIN", "L", u_top - 45.4, "VIN (5V)"), ("GND", "L", u_top - 44.4, "GND"), ("3V3", "L", u_top - 41.0, "3V3")]
    for (n, pos, trig, echo), yt in zip(SENSORS, tops):
        lp.append((trig, "L", u_top - (yt - 0.8), trig))
        lp.append((echo, "L", u_top - (yt - 1.6), {"GPIO36": "GPIO36 VP", "GPIO39": "GPIO39 VN"}.get(echo, echo)))
    rp = [(g, "R", u_top - y, g) for (n, g, _), y in zip(CHANNELS, rows)]
    u1 = Block(7.5, u_top - u_bot, lp + rp, center="**U1\nESP32\nDevKit\nWROOM-32")
    sh.part("U1", u1.at((ux, u_bot)), {p[0]: p[0] for p in lp + rp})
    vx, vy = sh.pin("U1", "VIN")
    sh.vdd((vx - 1.1, vy), "+5V", V5)
    sh.wire(find("J1.2", "U1.VIN"), [(vx - 1.1, vy), (vx, vy)])
    gx, gy = sh.pin("U1", "GND")
    sh.gnd((gx - 1.9, gy))
    sh.wire(find("U1.GND", "TB2.GND"), [(gx, gy), (gx - 1.9, gy)])
    sh.nc("U1", "3V3", label="3V3: do NOT feed 5 V", side="left", color=WARN_C)

    # ---- ultrasonic sensors with ECHO dividers (left of the ESP32)
    for (n, pos, trig, echo), yt in zip(SENSORS, tops):
        Sx, Rt, Rb = f"S{n}", f"R{n + 3}", f"R{n + 7}"
        sb = Block(4.4, 3.4, [("VCC", "R", 0.5, None), ("TRIG", "R", 1.3, None), ("ECHO", "R", 2.1, None),
                              ("GND", "R", 2.9, None)])
        sh.part(Sx, sb.at((0.3, yt - 2.9)), {p: p for p in ("VCC", "TRIG", "ECHO", "GND")})
        sh.text((0.3, yt + 0.95), f"{Sx} · {pos}", weight="bold")
        sh.text((1.5, yt - 1.2), "HC-SR04", ha="center", check=False)
        vx, vy = sh.pin(Sx, "VCC")
        sh.vdd((vx + 0.8, vy), "+5V", V5)
        sh.wire(find("J1.2", f"{Sx}.VCC"), [(vx, vy), (vx + 0.8, vy)])
        gx, gy = sh.pin(Sx, "GND")
        sh.gnd((gx + 0.8, gy))
        sh.wire(find(f"{Sx}.GND", "TB2.GND"), [(gx, gy), (gx + 0.8, gy)])
        sh.wire(find(f"U1.{trig}", f"{Sx}.TRIG"), [sh.pin(Sx, "TRIG"), sh.pin("U1", trig)])
        ex, ey = sh.pin(Sx, "ECHO")
        rt = elm.Resistor().endpoints((7.4, ey), (9.8, ey)).color(PART_C)
        sh.part(Rt, rt, {"1": "start", "2": "end"})
        sh.text((8.6, ey - 0.42), f"{Rt} 1 kΩ", ha="center", va="top")
        nx = 12.6
        rb = elm.Resistor().endpoints((nx, ey), (nx, ey - 2.0)).color(PART_C)
        sh.part(Rb, rb, {"1": "start", "2": "end"})
        sh.text((nx + 0.35, ey - 1.0), f"{Rb} 2 kΩ", ha="left")
        gpx, gpy = sh.pin("U1", echo)
        sh.wire(find(f"{Sx}.ECHO", f"{Rt}.1"), [(ex, ey), sh.pin(Rt, "1")])
        sh.wire(find(f"{Rt}.2", f"U1.{echo}"), [sh.pin(Rt, "2"), (nx, ey), (gpx, gpy)])
        sh.wire(find(f"U1.{echo}", f"{Rb}.1"), [sh.pin(Rb, "1"), (gpx, gpy)])
        sh.gnd((nx, ey - 2.5))
        sh.wire(find(f"{Rb}.2", "TB2.GND"), [sh.pin(Rb, "2"), (nx, ey - 2.5)])

    # ---- relay modules and motors (right of the ESP32)
    motor_txt = {1: ("LEFT propulsion motor", "Rhino IG32 12 V"), 2: ("RIGHT propulsion motor", "Rhino IG32 12 V"),
                 3: ("CONVEYOR motor", "assumed 12 V DC — verify")}
    for (n, gpio, _), yc in zip(CHANNELS, rows):
        K, M, D, C, R = f"K{n}", f"M{n}", f"D{n}", f"C{n}", f"R{n}"
        k = Block(5.8, 2.8, [("IN", "L", 1.4, None), ("DC+", "T", 1.0, None), ("DC-", "B", 1.0, None),
                             ("COM", "R", 0.6, None), ("NO", "R", 1.4, None), ("NC", "R", 2.2, None)],
                  center=f"**{K} FC65\njumper H")
        sh.part(K, k.at((34.4, yc - 1.4)), {p: p for p in ("IN", "DC+", "DC-", "COM", "NO", "NC")})
        gx_, gy_ = sh.pin("U1", gpio)
        sh.wire(find(f"U1.{gpio}", f"{K}.IN"), [(gx_, gy_), sh.pin(K, "IN")])
        nx = 31.6
        dmx, dmy = sh.pin(K, "DC-")
        r = elm.Resistor().endpoints((nx, yc), (nx, dmy)).color(PART_C)
        sh.part(R, r, {"1": "start", "2": "end"})
        sh.text((nx + 0.35, yc - 1.0), f"{R}\n10 kΩ", ha="left")
        sh.wire(find(f"{K}.IN", f"{R}.1"), [sh.pin(K, "IN"), sh.pin(R, "1")])
        sh.wire(find(f"{R}.2", f"{K}.DC-"), [sh.pin(R, "2"), (dmx, dmy)])
        sh.gnd((dmx, dmy - 0.5))
        sh.wire(find("TB2.GND", f"{K}.DC-"), [(dmx, dmy), (dmx, dmy - 0.5)])
        px, py = sh.pin(K, "DC+")
        sh.vdd((px, py + 0.5), "+12V_BUS", V12)
        sh.wire(find("TB1.+", f"{K}.DC+"), [(px, py + 0.5), (px, py)])
        cx, cy = sh.pin(K, "COM")
        sh.vdd((cx + 0.9, py + 0.5), "+12V_BUS", V12)
        sh.wire(find("TB1.+", f"{K}.COM"), [(cx + 0.9, py + 0.5), (cx + 0.9, cy), (cx, cy)])
        sh.nc(K, "NC", cid=find(f"{K}.NC", None), label="n.c.")
        mx, top, bot = 44.6, yc, yc - 2.8
        sh.part(M, elm.Motor().endpoints((mx, top), (mx, bot)).color(PART_C), {"+": "start", "-": "end"})
        sh.text((mx - 0.6, yc - 0.95), "+", fs=9, weight="bold", ha="right")
        sh.text((mx - 0.6, yc - 1.85), "−", fs=9, weight="bold", ha="right")
        dx_, cx_ = 46.8, 49.4
        sh.part(D, elm.Diode().endpoints((dx_, bot), (dx_, top)).scale(1.5).color(PART_C), {"A": "start", "K": "end"})
        sh.part(C, elm.Capacitor().endpoints((cx_, top), (cx_, bot)).color(PART_C), {"1": "start", "2": "end"})
        nox, noy = sh.pin(K, "NO")
        sh.wire(find(f"{K}.NO", f"{M}.+"), [(nox, noy), (mx, noy)])
        sh.wire(find(f"{D}.K", f"{M}.+"), [sh.pin(D, "K"), sh.pin(M, "+")])
        sh.wire(find(f"{C}.1", f"{M}.+"), [sh.pin(C, "1"), sh.pin(M, "+")])
        sh.wire(find(f"{D}.A", f"{M}.-"), [sh.pin(D, "A"), sh.pin(M, "-")])
        sh.wire(find(f"{C}.2", f"{M}.-"), [sh.pin(C, "2"), sh.pin(M, "-")])
        sh.gnd((mx, bot - 0.5))
        sh.wire(find(f"{M}.-", "TB2.GND"), [sh.pin(M, "-"), (mx, bot - 0.5)])
        sh.text((dx_ + 0.45, yc - 0.5), "band", fs=FS, color=WARN_C)
        sh.text((mx + 0.5, bot - 0.45), f"{D} 1N5408 / SB560", va="top")
        sh.text((cx_ + 0.35, yc - 1.4), f"{C} 0.1 µF\n(optional)", ha="left")
        t1, t2 = motor_txt[n]
        sh.text((53.6, yc - 0.9), f"{M}  {t1}", weight="bold")
        sh.text((53.6, yc - 1.6), t2, color=WARN_C if n == 3 else NOTE_C)

    sh.note(30.6, 15.7, 40.0, [
        ("1.", "Fit F1 (15 A) first, within 15 cm of BT1 +. Connect battery → panel → loads; disconnect in reverse."),
        ("2.", "NO wire from BT1 − to the ground bus: battery − reaches ground only through U3 (BAT− → LOAD−). "
               "SW1 is on the LOAD+ line."),
        ("3.", "+12V_BUS = terminal block TB1 after SW1. Ground symbol = ground bus TB2 (star point). "
               "+5V = UBEC output after J1."),
        ("4.", "Relays: jumper on H; IN must read ≈ 0 V before it is wired to the ESP32. Use NO, never NC. "
               "Diode band (cathode) on motor + / NO wire."),
        ("5.", "ECHO through 1 kΩ / 2 kΩ: 5 V × 2/3 = 3.33 V. Never feed 5 V into 3V3. "
               "USB programming: SW1 OFF, unplug J1."),
    ], title=f"Key rules (from {SOURCE}; full detail in boat_circuit_diagram.pdf)")
    legend_strip(sh, 0.2, 3.25, TB_U[0] - 0.6)
    sh.finish()
    return sh


def crosscheck_simple(sh):
    main = [c for c in NETLIST if c.variant == "main"]
    ids = {c.id for c in main}
    missing = sorted(ids - sh.drawn)
    extra = sorted(sh.drawn - ids)
    same = net_groups(main) == net_groups([c for c in main if c.id in sh.drawn])
    errors = [f"not drawn: {m} {CONN[m].a}->{CONN[m].b}" for m in missing]
    errors += [f"not in the main netlist: {e}" for e in extra]
    errors += [] if same else ["connectivity differs from the netlist"]
    errors += sh.errors
    print("=" * 78)
    print("ONE-PAGE DIAGRAM CHECK")
    print(f"main-build entries: {len(ids)}; drawn: {len(ids & sh.drawn)}; extra: {len(extra)}; "
          f"nets identical to netlist: {'yes' if same else 'NO'}; "
          f"{len(sh.wires)} wire paths, {len(sh.warnings)} layout warnings")
    for w in sh.warnings:
        print("    warning:", w)
    print("PASSED" if not errors else f"FAILED: {len(errors)} error(s)")
    for e in errors:
        print("  ERROR:", e)
    return errors


def write_csv():
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "net", "from_part", "from_pin", "to_part", "to_pin", "wire_colour (CONNECTIONS.md)",
                    "drawn_colour", "gauge", "section", "variant", "note", "sheets"])
        for c in NETLIST:
            w.writerow([c.id, c.net, c.a[0], c.a[1], c.b[0] if c.b else "(not connected)", c.b[1] if c.b else "",
                        c.colour, c.draw, c.gauge, c.section, c.variant, c.note,
                        " ".join(str(s) for s in sorted(c.sheets))])


def render_png(pdf, outdir, dpi=110, prefix="sheet"):
    import pypdfium2 as pdfium
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    paths = []
    for i in range(len(doc)):
        img = doc[i].render(scale=dpi / 72).to_pil()
        p = outdir / f"{prefix}{i + 1}.png"
        img.save(p)
        paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--png", metavar="DIR", help="also render each sheet to PNG in DIR")
    ap.add_argument("--dpi", type=int, default=110)
    args = ap.parse_args()

    ov_sheet, ov = sheet_overview()
    sheets = [ov_sheet, sheet_power(), sheet_relays(), sheet_esp32(), sheet_fallback(), sheet_reference()]
    errors = crosscheck(sheets[1:], ov) + [f"sheet 1: {e}" for e in ov_sheet.errors]
    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(OUT_PDF, metadata={"Title": f"{PROJECT} — circuit diagram, rev {REV}",
                                     "Subject": f"Generated from {SOURCE}", "Creator": "tools/make_schematic.py",
                                     "Author": "tools/make_schematic.py"}) as pdf:
        for s in sheets:
            pdf.savefig(s.fig)
            plt.close(s.fig)
    write_csv()
    print(f"wrote {OUT_PDF.relative_to(ROOT)} ({len(sheets)} sheets) and {OUT_CSV.relative_to(ROOT)}")
    simple = sheet_simple()
    errors += crosscheck_simple(simple)
    with PdfPages(OUT_SIMPLE, metadata={"Title": f"{PROJECT} — complete circuit (one page), rev {REV}",
                                        "Subject": f"Generated from {SOURCE}", "Creator": "tools/make_schematic.py",
                                        "Author": "tools/make_schematic.py"}) as pdf:
        pdf.savefig(simple.fig)
        plt.close(simple.fig)
    print(f"wrote {OUT_SIMPLE.relative_to(ROOT)} (1 page)")
    if args.png:
        for p in render_png(OUT_PDF, args.png, args.dpi) + render_png(OUT_SIMPLE, args.png, args.dpi, "simple"):
            print("rendered", p)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
