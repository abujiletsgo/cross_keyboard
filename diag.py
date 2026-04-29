#!/usr/bin/env python3
"""
Cross Corne GPIO Diagnostic Tool
=================================
Plug in ONE half via USB. Run this script. Press each key when prompted.
The script records what character comes out and maps it to a ZMK matrix position.

Usage:
    python3 diag.py right   # diagnose right side
    python3 diag.py left    # diagnose left side
"""

import sys, tty, termios, os

# ── ZMK matrix position → character mapping (from diagnostic keymap) ──────────
# Position N outputs:  0-9 → '0'-'9', 10-35 → 'a'-'z', 36-41 → F1-F6
def pos_to_char(n):
    if 0 <= n <= 9:
        return str(n)
    elif 10 <= n <= 35:
        return chr(ord('a') + n - 10)
    else:
        return f"F{n - 35}"

# Inverse map: char → position
char_to_pos = {}
for p in range(42):
    char_to_pos[pos_to_char(p)] = p

# ── Physical position descriptions ─────────────────────────────────────────────
# Matrix transform order (per cross_corne.dtsi):
#   Row 0: L0-L5 (left outer→inner), R0-R5 (right inner→outer in keymap = left→right visually)
#   Row 1: same
#   Row 2: same
#   Thumbs: LT0-LT2 (left), RT0-RT2 (right)
#
# VISUAL layout:
#   Right side top row (left→right): pos6 pos7 pos8 pos9 pos10 pos11
#   Right side mid row:              pos18 pos19 pos20 pos21 pos22 pos23
#   Right side bot row:              pos30 pos31 pos32 pos33 pos34 pos35
#   Right thumbs (left→right):       pos39 pos40 pos41

RIGHT_KEYS = [
    # (position, description)
    (6,  "RIGHT | TOP ROW    | key 1 (leftmost,  adjacent to gap)"),
    (7,  "RIGHT | TOP ROW    | key 2"),
    (8,  "RIGHT | TOP ROW    | key 3"),
    (9,  "RIGHT | TOP ROW    | key 4"),
    (10, "RIGHT | TOP ROW    | key 5"),
    (11, "RIGHT | TOP ROW    | key 6 (rightmost, pinky)"),
    (18, "RIGHT | MIDDLE ROW | key 1 (leftmost,  adjacent to gap)  ← H in QWERTY"),
    (19, "RIGHT | MIDDLE ROW | key 2  ← J in QWERTY"),
    (20, "RIGHT | MIDDLE ROW | key 3  ← K in QWERTY"),
    (21, "RIGHT | MIDDLE ROW | key 4  ← L in QWERTY"),
    (22, "RIGHT | MIDDLE ROW | key 5  ← ; in QWERTY"),
    (23, "RIGHT | MIDDLE ROW | key 6 (rightmost, pinky)"),
    (30, "RIGHT | BOTTOM ROW | key 1 (leftmost,  adjacent to gap)"),
    (31, "RIGHT | BOTTOM ROW | key 2"),
    (32, "RIGHT | BOTTOM ROW | key 3"),
    (33, "RIGHT | BOTTOM ROW | key 4"),
    (34, "RIGHT | BOTTOM ROW | key 5"),
    (35, "RIGHT | BOTTOM ROW | key 6 (rightmost, pinky)"),
    (39, "RIGHT | THUMB ROW  | key 1 (leftmost)"),
    (40, "RIGHT | THUMB ROW  | key 2"),
    (41, "RIGHT | THUMB ROW  | key 3 (rightmost)"),
]

LEFT_KEYS = [
    (0,  "LEFT  | TOP ROW    | key 1 (leftmost,  pinky)"),
    (1,  "LEFT  | TOP ROW    | key 2"),
    (2,  "LEFT  | TOP ROW    | key 3"),
    (3,  "LEFT  | TOP ROW    | key 4"),
    (4,  "LEFT  | TOP ROW    | key 5"),
    (5,  "LEFT  | TOP ROW    | key 6 (rightmost, adjacent to gap)"),
    (12, "LEFT  | MIDDLE ROW | key 1 (leftmost,  pinky)"),
    (13, "LEFT  | MIDDLE ROW | key 2  ← A in QWERTY"),
    (14, "LEFT  | MIDDLE ROW | key 3  ← S in QWERTY"),
    (15, "LEFT  | MIDDLE ROW | key 4  ← D in QWERTY"),
    (16, "LEFT  | MIDDLE ROW | key 5  ← F in QWERTY"),
    (17, "LEFT  | MIDDLE ROW | key 6 (rightmost, adjacent to gap)  ← G in QWERTY"),
    (24, "LEFT  | BOTTOM ROW | key 1 (leftmost,  pinky)"),
    (25, "LEFT  | BOTTOM ROW | key 2"),
    (26, "LEFT  | BOTTOM ROW | key 3"),
    (27, "LEFT  | BOTTOM ROW | key 4"),
    (28, "LEFT  | BOTTOM ROW | key 5"),
    (29, "LEFT  | BOTTOM ROW | key 6 (rightmost, adjacent to gap)"),
    (36, "LEFT  | THUMB ROW  | key 1 (leftmost)"),
    (37, "LEFT  | THUMB ROW  | key 2"),
    (38, "LEFT  | THUMB ROW  | key 3 (rightmost)"),
]

# ── GPIO reference table ────────────────────────────────────────────────────────
# Based on the current firmware:
#   row-gpios:  [gpio0 2, gpio0 29, gpio0 31, gpio0 11]  (index 0-3)
#   col-gpios:  [gpio0 22, gpio0 24, gpio1 0, gpio1 15, gpio1 13, gpio1 11]  (index 0-5, outer→inner)
#   col-offset: 6 for right side

def zmk_pos_to_gpio(pos, side):
    rows_gpio = ["gpio0 2", "gpio0 29", "gpio0 31", "gpio0 11"]
    cols_gpio = ["gpio0 22", "gpio0 24", "gpio1 0", "gpio1 15", "gpio1 13", "gpio1 11"]

    # Determine row and col from position
    # Positions 0-11: row 0, 12-23: row 1, 24-35: row 2, 36-41: row 3
    if pos <= 11:
        row_idx = 0
    elif pos <= 23:
        row_idx = 1
    elif pos <= 35:
        row_idx = 2
    else:
        row_idx = 3

    # Col within row
    if pos <= 35:
        within_row = pos % 12
        if within_row < 6:
            # Left side
            col_idx = within_row  # 0=outermost left, 5=innermost left
            side_label = "left"
        else:
            # Right side: positions 6-11 in row → col indices in reversed order
            # pos 6 = RC(r,11) = phys col 5; pos 11 = RC(r,6) = phys col 0
            col_idx = 11 - within_row  # pos6→5, pos11→0
            side_label = "right"
    else:
        # Thumb keys
        if pos in [36, 37, 38]:
            within = pos - 36  # 0,1,2
            col_idx = 3 + within  # thumb cols are 3,4,5
            side_label = "left"
        else:
            within = pos - 39   # 0,1,2
            col_idx = 5 - within  # pos39→5, pos41→3
            side_label = "right"

    row_g = rows_gpio[row_idx] if row_idx < len(rows_gpio) else "?"
    col_g = cols_gpio[col_idx] if col_idx < len(cols_gpio) else "?"
    return row_g, col_g, side_label

# ── Terminal raw input ──────────────────────────────────────────────────────────
def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.buffer.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch

def read_key():
    ch = getch()
    if ch == b'\x03':
        raise KeyboardInterrupt
    if ch == b'\x1b':
        return 'ESC'
    if ch == b'\r' or ch == b'\n':
        return 'ENTER'
    try:
        return ch.decode('utf-8')
    except Exception:
        return f"0x{ch.hex()}"

# ── Main ────────────────────────────────────────────────────────────────────────
def run(side):
    keys = RIGHT_KEYS if side == 'right' else LEFT_KEYS
    results = {}  # position → char pressed

    print(f"\n{'='*70}")
    print(f"  CROSS CORNE DIAGNOSTIC — {side.upper()} SIDE")
    print(f"{'='*70}")
    print("  Make sure ONLY the keyboard is in focus (this terminal window).")
    print("  For each prompt: press the physical key described, or ESC to skip.\n")

    for expected_pos, desc in keys:
        expected_char = pos_to_char(expected_pos)
        print(f"  {desc}")
        print(f"  Expected output char: '{expected_char}'  |  Press key (ESC=skip): ", end='', flush=True)

        got = read_key()
        if got == 'ESC':
            results[expected_pos] = None
            print("skipped")
        else:
            results[expected_pos] = got
            actual_pos = char_to_pos.get(got)
            if actual_pos == expected_pos:
                print(f"'{got}' ✓ correct")
            elif actual_pos is not None:
                print(f"'{got}' ✗ → got position {actual_pos} instead")
            else:
                print(f"'{got}' (non-diagnostic char)")
        print()

    # ── Report ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  RESULTS — {side.upper()} SIDE")
    print(f"{'='*70}")
    print(f"  {'Physical key':<50}  {'Got':<6}  {'ZMK pos':<8}  {'Row GPIO':<12}  {'Col GPIO'}")
    print(f"  {'-'*50}  {'-'*6}  {'-'*8}  {'-'*12}  {'-'*12}")

    for expected_pos, desc in keys:
        got = results.get(expected_pos)
        actual_pos = char_to_pos.get(got) if got else None
        row_g, col_g, _ = zmk_pos_to_gpio(expected_pos, side)
        if got is None:
            status = "SKIP"
        elif actual_pos == expected_pos:
            status = f"OK  '{got}'"
        elif actual_pos is not None:
            row_g2, col_g2, _ = zmk_pos_to_gpio(actual_pos, side)
            status = f"GOT pos {actual_pos} (r:{row_g2}, c:{col_g2})"
        else:
            status = f"'{got}' (unknown)"
        label = desc.split("|")[-1].strip()
        print(f"  {label:<50}  {status}")

    print()
    no_output = [desc.split("|")[-1].strip() for p, desc in keys if results.get(p) is None]
    wrong = [(desc.split("|")[-1].strip(), results[p]) for p, desc in keys
             if results.get(p) and char_to_pos.get(results[p]) != p]

    if no_output:
        print(f"  Keys with NO output ({len(no_output)}):")
        for k in no_output:
            print(f"    - {k}")
    if wrong:
        print(f"\n  Keys with WRONG output ({len(wrong)}):")
        for k, c in wrong:
            print(f"    - {k} → '{c}'")
    if not no_output and not wrong:
        print("  All keys correct!")

    print(f"\n  Paste this entire output and send it back.")

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('left', 'right'):
        print("Usage: python3 diag.py right   OR   python3 diag.py left")
        sys.exit(1)
    try:
        run(sys.argv[1])
    except KeyboardInterrupt:
        print("\n\nAborted.")
