# Acer B196L: 1280x1024 @ 50 Hz

Done on 6 November 2026 on one B196L (19", 1280x1024, VGA only).

## Firmware

| | |
|---|---|
| scaler | Realtek RTD2281 |
| flash | Macronix MX25L2005, 256 KB, JEDEC C2 20 12, status 0C |
| I2C devices on DDC | 0x37 (DDC/CI), 0x4A (ISP), 0x4B, 0x50 (EDID) |
| original dump, md5 | `720f2bd92cf391d0d9caca3f02b3c585` |
| mode table | 0x11B00, 156 records |
| patched, md5 | `13721a4298b616e39903f0f1c9b99965` |

If your dump has the same md5, `modetable.py preset q50` gives exactly the
patched image. A different md5 just means another firmware build; the tool
finds the table itself.

## The change

One record at 0x11C1D, 15 of its 19 bytes:

```
before  03 0480 0366 02A2 02EE 0A 0A 0640 0384 0180 0023   1152x870  67.4 kHz 75.0 Hz
after   0F 0500 0400 0216 01F5 0A 0A 07CE 042B 029E 0028   1280x1024 53.4 kHz 50.1 Hz
```

The new mode: polarities any, 1998 samples per line (two per pixel of a 999
dot line at 53.333 MHz), 1067 lines, picture starting 670 samples after
hsync and 40 lines after vsync. Tolerances stay at ±1.0 kHz / ±1.0 Hz.

## Result

The monitor locks to the ComboLite Q50 output: 999 x 1066 at 53.333 MHz,
with the frame stretched to 1067 (or cut to 1065) lines when needed to stay in
step with the Vector-06C. The line length never changes; the monitor
rebuilds its sampling clock from hsync, and any wobble there shows as
shimmering pixel edges. The first Auto presses may set the sampling clock
wrong (soft pixel edges); pressing Auto again fixed it.

## Acer V196L b

The same tool also finds the table of a V196L b firmware (table at 0x2014,
162 records) and patches it the same way. That firmware has a different
start (`02 11 2D`); the patch was tested on the image only, not on a monitor.
