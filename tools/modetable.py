#!/usr/bin/env python3
"""List or edit the input mode table of an RTD2281 monitor firmware image.

  modetable.py list IMAGE
  modetable.py add IMAGE OUT --mode W H HFREQ VFREQ HTOTAL VTOTAL HSTART VSTART
                             [--pol 0F] [--tol 10 10] [--replace W H HFREQ VFREQ]
  modetable.py preset q50 IMAGE OUT      the 1280x1024@50 mode for the Vector-06C ComboLite

HFREQ is in 0.1 kHz, VFREQ in 0.1 Hz (53.4 kHz -> 534, 50.1 Hz -> 501).
`add` overwrites one existing record (--replace, default 1152x870@75 Mac mode)
and refuses when a mode with the same size and frequencies within tolerance
is already there, because the monitor would pick that one first.

Record layout (19 bytes, big-endian), worked out on Acer A221HQL, V196L and
B196L firmwares:
  pol   1 B  low nibble: accepted sync polarities (0F = any), bits 4-5: flags
  W, H  2+2  active size
  Hf    2    line frequency, 0.1 kHz
  Vf    2    frame frequency, 0.1 Hz
  tolH  1    Hf tolerance, 0.1 kHz
  tolV  1    Vf tolerance, 0.1 Hz
  Htot  2    samples per line: the ADC clock is Hf * Htot
  Vtot  2    lines per frame (the firmware does not seem to read it)
  Hst   2    first active sample after the start of hsync
  Vst   2    first active line after the start of vsync
The table is found by the 1280x1024@60 record (W, H, 64.0 kHz, 60.0 Hz),
which every RTD2281 firmware for a 1280x1024 or larger panel has.
"""
import argparse, struct, sys

F = '>B4H2B4H'
SIZE = 19
ANCHOR = bytes.fromhex('0500040002800258')
PRESETS = {
    # Vector-06C ComboLite rev4 VGA "Q50": 999 dots per line at 53.333 MHz
    # shown as 1998 samples, 1066-1067 lines, 53.39 kHz / 50.08 Hz, sync +/+
    'q50': dict(mode=(1280, 1024, 534, 501, 1998, 1067, 670, 40), pol=0x0F, tol=(10, 10)),
}


def sane(r):
    return (r[0] & 0x0F and 100 <= r[1] <= 2600 and 100 <= r[2] <= 1700 and 50 <= r[3] <= 1500
            and 200 <= r[4] <= 1300 and r[5] <= 40 and r[6] <= 40 and r[1] < r[7] <= 4000 and r[2] <= r[8] <= 2000)


def table(d):
    hits = [i - 1 for i in range(1, len(d) - 8) if d[i:i + 8] == ANCHOR]
    if len(hits) != 1:
        sys.exit('1280x1024@60 record found %d times, cannot locate the table' % len(hits))
    o = hits[0]
    while o - SIZE >= 0 and sane(struct.unpack_from(F, d, o - SIZE)):
        o -= SIZE
    recs = []
    while o + SIZE <= len(d):
        r = struct.unpack_from(F, d, o)
        if not sane(r):
            break
        recs.append((o, r))
        o += SIZE
    return recs


def show(o, r):
    return ('%05X  pol %02X  %4dx%-4d  %5.1f kHz %5.1f Hz  tol %.1f/%.1f  total %4dx%-4d  start %d,%d'
            % (o, r[0], r[1], r[2], r[3] / 10, r[4] / 10, r[5] / 10, r[6] / 10, r[7], r[8], r[9], r[10]))


def add(src, dst, mode, pol, tol, victim):
    d = bytearray(open(src, 'rb').read())
    recs = table(d)
    print('table at %05X, %d records' % (recs[0][0], len(recs)))
    w, h, hf, vf = mode[:4]
    for o, r in recs:
        if r[1] == w and r[2] == h and abs(r[3] - hf) <= max(r[5], tol[0]) and abs(r[4] - vf) <= max(r[6], tol[1]):
            sys.exit('a close mode is already there:\n  ' + show(o, r))
    v = [(o, r) for o, r in recs if r[1:5] == tuple(victim)]
    if len(v) != 1:
        sys.exit('record to replace %dx%d %.1f kHz %.1f Hz found %d times' % (*victim[:2], victim[2] / 10, victim[3] / 10, len(v)))
    o, r = v[0]
    new = struct.pack(F, pol, w, h, hf, vf, tol[0], tol[1], *mode[4:])
    print('replace ' + show(o, r))
    print('   with ' + show(o, struct.unpack(F, new)))
    d[o:o + SIZE] = new
    open(dst, 'wb').write(d)
    print('saved %s (%d bytes changed)' % (dst, sum(1 for a, b in zip(open(src, 'rb').read(), d) if a != b)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='op', required=True)
    p = sp.add_parser('list'); p.add_argument('image')
    p = sp.add_parser('add'); p.add_argument('image'); p.add_argument('out')
    p.add_argument('--mode', type=int, nargs=8, required=True, metavar='N')
    p.add_argument('--pol', type=lambda s: int(s, 16), default=0x0F)
    p.add_argument('--tol', type=int, nargs=2, default=(10, 10))
    p.add_argument('--replace', type=int, nargs=4, default=(1152, 870, 674, 750), metavar='N')
    p = sp.add_parser('preset'); p.add_argument('name', choices=sorted(PRESETS)); p.add_argument('image'); p.add_argument('out')
    p.add_argument('--replace', type=int, nargs=4, default=(1152, 870, 674, 750), metavar='N')
    a = ap.parse_args()
    if a.op == 'list':
        recs = table(open(a.image, 'rb').read())
        print('table at %05X, %d records' % (recs[0][0], len(recs)))
        for o, r in recs:
            print(show(o, r))
    elif a.op == 'add':
        add(a.image, a.out, a.mode, a.pol, a.tol, a.replace)
    else:
        pr = PRESETS[a.name]
        add(a.image, a.out, pr['mode'], pr['pol'], pr['tol'], a.replace)


if __name__ == '__main__':
    main()
