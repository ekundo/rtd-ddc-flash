#!/usr/bin/env python3
"""Host side of rtdddc: dump, verify and write the SPI flash of a Realtek
monitor scaler through an Arduino Uno running arduino/rtdddc.

  rtdddc.py PORT scan                 bus check: line voltages, I2C addresses
  rtdddc.py PORT info                 enter ISP, show flash JEDEC id and status
  rtdddc.py PORT dump FILE            read the whole flash twice, save if equal
  rtdddc.py PORT verify FILE          compare the flash with FILE
  rtdddc.py PORT write FILE [--yes]   rewrite only the 4 KB sectors that differ

Every read and program is checked against the scaler's own CRC. `write`
clears the flash write protection for the duration and puts it back.
After `write`, power-cycle the monitor (unplug it for ~10 s).
"""
import argparse, sys, time

try:
    import serial
except ImportError:
    sys.exit('needs pyserial: python3 -m pip install pyserial')

HELLO, PROBE, READ, ERASE4K, PROGRAM, WRSR, RDSR, SCAN = 1, 2, 3, 4, 5, 6, 7, 8
STATUS = {1: 'bad frame', 2: 'unknown command', 3: 'no answer at I2C 0x4A (wiring, ground on VGA 10, monitor off?)',
          4: 'scaler did not enter ISP mode', 5: 'timeout talking to the scaler', 6: 'run info first',
          7: 'bad arguments'}
SECTOR, PAGE, CHUNK = 4096, 256, 256


def crc8(data):
    """CRC the scaler computes over a flash range: CRC-8, poly 0x07, init 0."""
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


class Programmer:
    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=0.1)
        time.sleep(2.0)                     # opening the port resets the Uno
        self.s.reset_input_buffer()
        v = self.call(HELLO)
        if not v.startswith(b'rtdddc'):
            sys.exit('unexpected answer %r -- is arduino/rtdddc uploaded?' % v)

    def call(self, cmd, payload=b'', timeout=3.0):
        n = len(payload)
        body = bytes([cmd, n & 0xFF, n >> 8]) + bytes(payload)
        self.s.write(b'\xA5' + body + bytes([sum(body) & 0xFF]))
        t0, buf = time.time(), b''

        def need(k):
            nonlocal buf
            while len(buf) < k:
                if time.time() - t0 > timeout:
                    raise IOError('no reply from the Uno (cmd %d)' % cmd)
                buf += self.s.read(k - len(buf))

        need(1)
        while buf[:1] != b'\x5A':
            buf = buf[1:]
            need(1)
        need(4)
        n = buf[2] | buf[3] << 8
        need(5 + n)
        frame = buf[1:5 + n]
        if sum(frame[:-1]) & 0xFF != frame[-1]:
            raise IOError('reply checksum error')
        if frame[0]:
            raise IOError(STATUS.get(frame[0], 'status %d' % frame[0]))
        return frame[3:-1]

    def probe(self):
        r = self.call(PROBE)
        return r[0] << 16 | r[1] << 8 | r[2], r[3]

    def read(self, addr, n):
        for attempt in range(3):
            r = self.call(READ, addr.to_bytes(3, 'big') + n.to_bytes(2, 'little'))
            data, chip = r[:n], r[n]
            if crc8(data) == chip:
                return data
        raise IOError('CRC mismatch reading %06X three times' % addr)

    def read_all(self, size, label=''):
        out, t0 = bytearray(), time.time()
        for a in range(0, size, CHUNK):
            out += self.read(a, CHUNK)
            if a % 0x4000 == 0 or a + CHUNK >= size:
                print('\r%s%06X / %06X  %3.0f s' % (label, a + CHUNK, size, time.time() - t0), end='', flush=True)
        print()
        return bytes(out)

    def status(self):
        return self.call(RDSR)[0]

    def set_status(self, v):
        self.call(WRSR, bytes([v]))

    def write_sector(self, addr, data):
        self.call(ERASE4K, addr.to_bytes(3, 'big'), timeout=5)
        for a in range(0, SECTOR, PAGE):
            page = data[a:a + PAGE]
            if page == b'\xff' * PAGE:
                continue
            chip = self.call(PROGRAM, (addr + a).to_bytes(3, 'big') + page)[0]
            if chip != crc8(page):
                raise IOError('CRC mismatch after programming %06X' % (addr + a))
        back = b''.join(self.read(addr + a, CHUNK) for a in range(0, SECTOR, CHUNK))
        if back != data:
            raise IOError('sector %06X reads back different' % addr)


def flash_size(jedec):
    cap = jedec & 0xFF
    if not 0x10 <= cap <= 0x18:
        sys.exit('cannot tell the flash size from JEDEC id %06X' % jedec)
    return 1 << cap


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('port', help='serial port of the Uno, e.g. /dev/cu.usbmodem1401, /dev/ttyACM0, COM3')
    ap.add_argument('op', choices=['scan', 'info', 'dump', 'verify', 'write'])
    ap.add_argument('file', nargs='?')
    ap.add_argument('--yes', action='store_true', help='write without asking')
    a = ap.parse_args()
    if a.op in ('dump', 'verify', 'write') and not a.file:
        ap.error('%s needs a FILE' % a.op)

    p = Programmer(a.port)
    if a.op == 'scan':
        r = p.call(SCAN)
        print('SDA %.2f V, SCL %.2f V (without pull-ups; a working bus sits near 5 V)'
              % (r[0] * 5 / 255, r[1] * 5 / 255))
        print('I2C devices:', ' '.join('0x%02X' % x for x in r[2:]) or 'none')
        print('scaler ISP address 0x4A', 'found' if 0x4A in r[2:] else 'NOT found')
        return

    jedec, st = p.probe()
    size = flash_size(jedec)
    print('flash JEDEC %06X, %d KB, status %02X' % (jedec, size // 1024, st))
    if a.op == 'info':
        return

    if a.op == 'dump':
        one = p.read_all(size, 'read 1 ')
        two = p.read_all(size, 'read 2 ')
        if one != two:
            bad = sum(1 for i in range(size) if one[i] != two[i])
            sys.exit('the two reads differ in %d bytes -- check wiring, nothing saved' % bad)
        open(a.file, 'wb').write(one)
        print('saved %s, both reads equal' % a.file)
        return

    img = open(a.file, 'rb').read()
    if len(img) != size:
        sys.exit('%s is %d bytes, the flash is %d' % (a.file, len(img), size))
    cur = p.read_all(size, 'read ')
    diff = [s for s in range(0, size, SECTOR) if cur[s:s + SECTOR] != img[s:s + SECTOR]]
    if a.op == 'verify':
        print('identical' if not diff else 'differs in sectors: ' + ' '.join('%06X' % s for s in diff))
        sys.exit(1 if diff else 0)

    if not diff:
        print('flash already equals %s, nothing to write' % a.file)
        return
    print('sectors to rewrite:', ' '.join('%06X' % s for s in diff))
    if not a.yes and input('write %d sector(s)? type yes: ' % len(diff)).strip() != 'yes':
        sys.exit('not written')
    if st & 0x9C:
        p.set_status(0x00)                 # clear block protection (BP bits, SRWD)
    try:
        for s in diff:
            p.write_sector(s, img[s:s + SECTOR])
            print('sector %06X written and verified' % s)
    finally:
        if st & 0x9C:
            p.set_status(st & 0x9C)
            print('status register back to %02X' % p.status())
    print('done. Unplug the monitor for ~10 s so the scaler restarts from the new firmware.')


if __name__ == '__main__':
    try:
        main()
    except (IOError, serial.SerialException) as e:
        sys.exit('error: %s' % e)
