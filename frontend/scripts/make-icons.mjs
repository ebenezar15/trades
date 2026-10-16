// Generates PNG app icons without native deps (pure JS rasteriser + zlib PNG encoder).
// Mirrors public/icons/icon.svg. Run: npm run icons
import { writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';

const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const BG = hex('#0b1220');
const RED = hex('#ef4444');
const GREEN = hex('#22c55e');
const BLUE = hex('#38bdf8');

function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size, px) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    px.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ]);
}

function render(size, { rounded, inset }) {
  const px = Buffer.alloc(size * size * 4);
  const s = size / 512;
  const sc = (v) => (inset ? 256 + (v - 256) * 0.75 : v) * s; // maskable: shrink artwork into safe zone
  const set = (x, y, c) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    const i = (y * size + x) * 4; px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = 255;
  };
  const r = rounded ? 96 * s : 0;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const cx = Math.max(r - x, 0, x - (size - 1 - r)); const cy = Math.max(r - y, 0, y - (size - 1 - r));
    if (cx * cx + cy * cy <= r * r) set(x, y, BG);
  }
  const rect = (x, y, w, h, c) => {
    for (let yy = Math.round(sc(y)); yy < Math.round(sc(y + h)); yy++)
      for (let xx = Math.round(sc(x)); xx < Math.round(sc(x + w)); xx++) set(xx, yy, c);
  };
  rect(104, 250, 56, 130, RED); rect(128, 220, 8, 190, RED);
  rect(228, 200, 56, 140, GREEN); rect(252, 160, 8, 210, GREEN);
  rect(352, 120, 56, 150, GREEN); rect(376, 90, 8, 210, GREEN);
  const pts = [[96, 420], [200, 330], [300, 300], [420, 150]];
  const w = 11 * s * (inset ? 0.75 : 1);
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i].map(sc); const [x1, y1] = pts[i + 1].map(sc);
    const steps = Math.ceil(Math.hypot(x1 - x0, y1 - y0));
    for (let t = 0; t <= steps; t++) {
      const x = x0 + ((x1 - x0) * t) / steps; const y = y0 + ((y1 - y0) * t) / steps;
      for (let dy = -w; dy <= w; dy++) for (let dx = -w; dx <= w; dx++)
        if (dx * dx + dy * dy <= w * w) set(Math.round(x + dx), Math.round(y + dy), BLUE);
    }
  }
  return png(size, px);
}

const out = new URL('../public/icons/', import.meta.url);
writeFileSync(new URL('icon-192.png', out), render(192, { rounded: true }));
writeFileSync(new URL('icon-512.png', out), render(512, { rounded: true }));
writeFileSync(new URL('icon-maskable-512.png', out), render(512, { rounded: false, inset: true }));
console.log('icons written');
