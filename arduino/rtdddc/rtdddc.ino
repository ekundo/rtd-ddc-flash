// rtdddc -- read and write the SPI flash of a Realtek RTD2281 / RTD266x
// monitor scaler through the VGA DDC pins, with an Arduino Uno.
//
// Wiring (VGA connector of the monitor, monitor powered on, no PC attached):
//   Uno A4 (SDA) -> VGA 12, Uno A5 (SCL) -> VGA 15, Uno GND -> VGA 10.
// Do not use VGA 5 for ground: on some monitors it is not connected.
// No pull-ups needed, the monitor has its own.
//
// The scaler answers at I2C address 0x4A. Register 0x6F bit 7 puts it into
// ISP mode (its 8051 stops), and registers 0x60..0x75 then drive the SPI
// flash. The register map is documented by the projects listed in README.
//
// Serial protocol, 115200 8N1, binary:
//   request  A5 cmd len_lo len_hi payload... sum
//   response 5A st  len_lo len_hi payload... sum
// sum = 8-bit sum of every byte after the A5/5A marker. st = 0 is success.
//
// Two RTD2281 details handled here (found on an Acer B196L, 2026):
//   * the first byte read out of data port 0x70 after starting a read is
//     stale, so it is re-read with a one-byte READ command;
//   * once that is fixed, the scaler CRC (0x72..0x75) matches the data and
//     is returned with every read and program for the host to check.

#include <Wire.h>

#define VERSION "rtdddc 1"
#define RTD 0x4A

enum { C_HELLO = 1, C_PROBE = 2, C_READ = 3, C_ERASE4K = 4, C_PROGRAM = 5,
       C_WRSR = 6, C_RDSR = 7, C_SCAN = 8 };
enum { S_OK = 0, S_BADFRAME = 1, S_BADCMD = 2, S_NOSLAVE = 3, S_NOISP = 4,
       S_TIMEOUT = 5, S_NOTREADY = 6, S_ARGS = 7 };

// SPI command classes for register 0x60 (bits 7..5)
enum { K_READ = 2, K_WREN = 3, K_EWSR = 4 };

static uint8_t buf[264];
static bool isp = false;

// ------------------------------------------------------------------ I2C
static bool wr(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(RTD);
  Wire.write(reg);
  Wire.write(v);
  return Wire.endTransmission() == 0;
}

static uint8_t rd(uint8_t reg) {
  Wire.beginTransmission(RTD);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom((uint8_t)RTD, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

static void rd_block(uint8_t reg, uint8_t* d, uint8_t n) {   // n <= 32
  Wire.beginTransmission(RTD);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom((uint8_t)RTD, n);
  for (uint8_t i = 0; i < n; i++) d[i] = Wire.available() ? Wire.read() : 0xFF;
}

static void wr_block(uint8_t reg, const uint8_t* d, uint8_t n) {  // n <= 16
  Wire.beginTransmission(RTD);
  Wire.write(reg);
  for (uint8_t i = 0; i < n; i++) Wire.write(d[i]);
  Wire.endTransmission();
}

// wait until (reg & mask) == want, up to ms milliseconds
static bool wait_reg(uint8_t reg, uint8_t mask, uint8_t want, uint16_t ms) {
  uint32_t t0 = millis();
  while ((rd(reg) & mask) != want)
    if (millis() - t0 > ms) return false;
  return true;
}

static void addr3(uint8_t base, uint32_t a) {
  wr(base, a >> 16); wr(base + 1, a >> 8); wr(base + 2, a);
}

// ------------------------------------------------------------------ flash
// One short SPI command through the scaler: opcode, up to 3 bytes out,
// up to 3 bytes back (registers 0x64..0x66 out, 0x67..0x69 in).
static bool spi_cmd(uint8_t kind, uint8_t op, uint8_t nr, uint8_t nw, uint32_t v, uint32_t* out) {
  uint8_t r60 = (kind << 5) | (nw << 3) | (nr << 1);
  wr(0x60, r60);
  wr(0x61, op);
  if (nw == 3) addr3(0x64, v);
  else if (nw == 2) { wr(0x64, v >> 8); wr(0x65, v); }
  else if (nw == 1) wr(0x64, v);
  wr(0x60, r60 | 1);
  if (!wait_reg(0x60, 1, 0, 100)) return false;
  uint32_t r = 0;
  for (uint8_t i = 0; i < nr; i++) r = (r << 8) | rd(0x67 + i);
  if (out) *out = r;
  return true;
}

static bool wait_wip(uint16_t ms) {            // flash status bit 0
  uint32_t t0 = millis(), s;
  do {
    if (!spi_cmd(K_READ, 0x05, 1, 0, 0, &s)) return false;
    if (!(s & 1)) return true;
  } while (millis() - t0 < ms);
  return false;
}

static bool chip_crc(uint32_t a0, uint32_t a1, uint8_t* crc) {
  addr3(0x64, a0);
  addr3(0x72, a1);
  wr(0x6F, 0x84);
  if (!wait_reg(0x6F, 0x02, 0x02, 200)) return false;
  *crc = rd(0x75);
  return true;
}

static uint8_t probe(uint8_t* out) {           // out: JEDEC id[3], status
  Wire.beginTransmission(RTD);
  if (Wire.endTransmission() != 0) return S_NOSLAVE;
  isp = false;
  for (uint8_t i = 0; i < 5 && !isp; i++) {
    wr(0x6F, 0x80);
    isp = rd(0x6F) & 0x80;
  }
  if (!isp) return S_NOISP;
  // opcodes the scaler uses on its own: WREN, EWSR, READ, FAST READ,
  // PAGE PROGRAM, RDSR -- common to the 25-series SPI flashes
  wr(0x62, 0x06); wr(0x63, 0x50); wr(0x6A, 0x03);
  wr(0x6B, 0x0B); wr(0x6D, 0x02); wr(0x6E, 0x05);
  uint32_t id = 0, st = 0;
  if (!spi_cmd(K_READ, 0x9F, 3, 0, 0, &id) || !spi_cmd(K_READ, 0x05, 1, 0, 0, &st)) return S_TIMEOUT;
  out[0] = id >> 16; out[1] = id >> 8; out[2] = id; out[3] = st;
  return S_OK;
}

static uint8_t flash_read(uint32_t a, uint16_t n, uint8_t* d) {   // n <= 256
  wr(0x60, 0x46);                  // READ class, 3 address bytes, read to port 0x70
  wr(0x61, 0x03);
  addr3(0x64, a);
  wr(0x60, 0x47);
  if (!wait_reg(0x60, 1, 0, 100)) return S_TIMEOUT;
  for (uint16_t i = 0; i < n; i += 32) rd_block(0x70, d + i, n - i < 32 ? n - i : 32);
  uint32_t b0;                     // RTD2281: the first byte from 0x70 is stale
  if (!spi_cmd(K_READ, 0x03, 1, 3, a, &b0)) return S_TIMEOUT;
  d[0] = b0;
  return chip_crc(a, a + n - 1, d + n) ? S_OK : S_TIMEOUT;
}

static uint8_t flash_program(uint32_t a, uint16_t n, const uint8_t* d, uint8_t* crc) {
  wr(0x71, n - 1);
  addr3(0x64, a);
  for (uint16_t i = 0; i < n; i += 16) wr_block(0x70, d + i, n - i < 16 ? n - i : 16);
  wr(0x6F, 0xA0);                  // program engine: WREN + PAGE PROGRAM
  if (!wait_reg(0x6F, 0x40, 0, 200) || !wait_wip(200)) return S_TIMEOUT;
  return chip_crc(a, a + n - 1, crc) ? S_OK : S_TIMEOUT;
}

// ------------------------------------------------------------------ serial
static void reply(uint8_t st, const uint8_t* p, uint16_t n) {
  uint8_t h[3] = { st, (uint8_t)n, (uint8_t)(n >> 8) }, s = 0;
  Serial.write(0x5A);
  for (uint8_t i = 0; i < 3; i++) { Serial.write(h[i]); s += h[i]; }
  for (uint16_t i = 0; i < n; i++) { Serial.write(p[i]); s += p[i]; }
  Serial.write(s);
  Serial.flush();
}

static int getb(uint32_t t0) {
  while (!Serial.available()) if (millis() - t0 > 1000) return -1;
  return Serial.read();
}

static uint32_t a24(const uint8_t* p) { return ((uint32_t)p[0] << 16) | ((uint16_t)p[1] << 8) | p[2]; }

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(100000);
  Wire.setWireTimeout(25000, true);   // a held-low bus must not hang the sketch
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  int c = getb(millis());
  if (c != 0xA5) return;
  uint32_t t0 = millis();
  int cmd = getb(t0), l0 = getb(t0), l1 = getb(t0);
  if (cmd < 0 || l0 < 0 || l1 < 0) return;
  uint16_t n = l0 | (l1 << 8);
  if (n > sizeof(buf)) { reply(S_BADFRAME, 0, 0); return; }
  uint8_t s = cmd + l0 + l1;
  for (uint16_t i = 0; i < n; i++) {
    int b = getb(t0);
    if (b < 0) return;
    buf[i] = b; s += b;
  }
  int cs = getb(t0);
  if (cs < 0 || (uint8_t)cs != s) { reply(S_BADFRAME, 0, 0); return; }

  digitalWrite(LED_BUILTIN, HIGH);
  uint8_t st = S_OK, out[4];
  uint32_t v;
  switch (cmd) {
  case C_HELLO:
    reply(S_OK, (const uint8_t*)VERSION, sizeof(VERSION) - 1);
    break;
  case C_SCAN: {                      // line levels with pull-ups off, then addresses
    pinMode(A4, INPUT); pinMode(A5, INPUT);
    delay(5);
    uint8_t m = 0;
    buf[m++] = analogRead(A4) >> 2;   // 0..255 for 0..5 V
    buf[m++] = analogRead(A5) >> 2;
    Wire.begin();
    Wire.setWireTimeout(25000, true);
    for (uint8_t a = 1; a < 127 && m < sizeof(buf); a++) {
      Wire.beginTransmission(a);
      if (Wire.endTransmission() == 0) buf[m++] = a;
    }
    isp = false;
    reply(S_OK, buf, m);
    break;
  }
  case C_PROBE:
    st = probe(out);
    reply(st, out, st == S_OK ? 4 : 0);
    break;
  case C_READ:                        // addr[3] len[2] -> data[len] crc
    if (!isp) { reply(S_NOTREADY, 0, 0); break; }
    v = buf[3] | (buf[4] << 8);
    if (n != 5 || v == 0 || v > 256) { reply(S_ARGS, 0, 0); break; }
    st = flash_read(a24(buf), v, buf);
    reply(st, buf, st == S_OK ? v + 1 : 0);
    break;
  case C_ERASE4K:                     // addr[3]
    if (!isp) { reply(S_NOTREADY, 0, 0); break; }
    if (n != 3) { reply(S_ARGS, 0, 0); break; }
    st = (spi_cmd(K_WREN, 0x20, 0, 3, a24(buf), 0) && wait_wip(1000)) ? S_OK : S_TIMEOUT;
    reply(st, 0, 0);
    break;
  case C_PROGRAM:                     // addr[3] data[1..256], one page -> crc
    if (!isp) { reply(S_NOTREADY, 0, 0); break; }
    if (n < 4 || n > 259 || ((a24(buf) & 0xFF) + n - 3) > 256) { reply(S_ARGS, 0, 0); break; }
    st = flash_program(a24(buf), n - 3, buf + 3, out);
    reply(st, out, st == S_OK ? 1 : 0);
    break;
  case C_WRSR:                        // status[1]
    if (!isp) { reply(S_NOTREADY, 0, 0); break; }
    if (n != 1) { reply(S_ARGS, 0, 0); break; }
    st = (spi_cmd(K_EWSR, 0x01, 0, 1, buf[0], 0) && spi_cmd(K_WREN, 0x01, 0, 1, buf[0], 0)
          && wait_wip(200)) ? S_OK : S_TIMEOUT;
    reply(st, 0, 0);
    break;
  case C_RDSR:
    if (!isp) { reply(S_NOTREADY, 0, 0); break; }
    st = spi_cmd(K_READ, 0x05, 1, 0, 0, &v) ? S_OK : S_TIMEOUT;
    out[0] = v;
    reply(st, out, st == S_OK ? 1 : 0);
    break;
  default:
    reply(S_BADCMD, 0, 0);
  }
  digitalWrite(LED_BUILTIN, LOW);
}
