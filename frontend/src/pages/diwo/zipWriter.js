/**
 * Minimal ZIP writer
 * ==================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Builds a standard ZIP archive in the browser with no dependency, so the
 * refactored sources can be handed back as one download that keeps the
 * repository's folder structure ("src/util/Helper.java" stays nested).
 *
 * Entries are DEFLATE-compressed through the platform's CompressionStream when
 * it is available and stored uncompressed otherwise, so the archive stays
 * readable by every unzip tool either way. Sizes are 32-bit — fine for source
 * text, and well under the 4 GB point where ZIP64 would be required.
 *
 * Layout written (APPNOTE 6.3.x):
 *   [local header + name + data] * n  →  [central directory] * n  →  EOCD
 */

const LOCAL_HEADER_SIG = 0x04034b50;
const CENTRAL_HEADER_SIG = 0x02014b50;
const EOCD_SIG = 0x06054b50;

const METHOD_STORE = 0;
const METHOD_DEFLATE = 8;

// Bit 11 tells the reader that the file name is UTF-8, which is what
// TextEncoder produces — without it, non-ASCII paths get mangled.
const FLAG_UTF8 = 0x0800;

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let c = i;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/**
 * Make a path safe to write inside an archive.
 *
 * Drops drive letters, leading slashes and any ".." segment, so extracting the
 * archive can never escape the folder the user chose.
 */
export function normalizeZipPath(value, fallback = "file") {
  const cleaned = String(value ?? "")
    .replace(/\\/g, "/")
    .replace(/^[a-zA-Z]:/, "")
    .split("/")
    .filter((part) => part && part !== "." && part !== "..")
    .join("/");
  return cleaned || fallback;
}

async function deflateRaw(bytes) {
  if (typeof CompressionStream === "undefined") return null;
  try {
    const compressed = new Blob([bytes])
      .stream()
      .pipeThrough(new CompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(compressed).arrayBuffer());
  } catch {
    // Any failure just means this entry is stored instead of deflated.
    return null;
  }
}

function dosDateTime(date) {
  const time =
    ((date.getHours() & 0x1f) << 11) |
    ((date.getMinutes() & 0x3f) << 5) |
    (Math.floor(date.getSeconds() / 2) & 0x1f);
  const day =
    (((date.getFullYear() - 1980) & 0x7f) << 9) |
    (((date.getMonth() + 1) & 0x0f) << 5) |
    (date.getDate() & 0x1f);
  return { time, date: day };
}

/**
 * Build a ZIP archive.
 *
 * @param {{path: string, content: string|Uint8Array}[]} entries
 * @param {{ date?: Date, compress?: boolean }} options
 * @returns {Promise<Blob>} archive ready to hand to a download link
 */
export async function createZip(entries, { date = new Date(), compress = true } = {}) {
  const encoder = new TextEncoder();
  const { time: dosTime, date: dosDate } = dosDateTime(date);

  const chunks = [];
  const central = [];
  const usedNames = new Set();
  let offset = 0;

  for (const entry of entries || []) {
    let name = normalizeZipPath(entry?.path);

    // Two entries cannot share a name, or the archive silently loses one.
    if (usedNames.has(name)) {
      const dot = name.lastIndexOf(".");
      const stem = dot > 0 ? name.slice(0, dot) : name;
      const ext = dot > 0 ? name.slice(dot) : "";
      let n = 2;
      while (usedNames.has(`${stem}(${n})${ext}`)) n += 1;
      name = `${stem}(${n})${ext}`;
    }
    usedNames.add(name);

    const nameBytes = encoder.encode(name);
    const raw =
      entry.content instanceof Uint8Array
        ? entry.content
        : encoder.encode(String(entry.content ?? ""));

    const deflated = compress ? await deflateRaw(raw) : null;
    // Only keep the compressed form when it is actually smaller.
    const useDeflate = deflated !== null && deflated.length < raw.length;
    const payload = useDeflate ? deflated : raw;
    const method = useDeflate ? METHOD_DEFLATE : METHOD_STORE;
    const checksum = crc32(raw);

    const local = new Uint8Array(30 + nameBytes.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, LOCAL_HEADER_SIG, true);
    localView.setUint16(4, 20, true);            // version needed to extract
    localView.setUint16(6, FLAG_UTF8, true);
    localView.setUint16(8, method, true);
    localView.setUint16(10, dosTime, true);
    localView.setUint16(12, dosDate, true);
    localView.setUint32(14, checksum, true);
    localView.setUint32(18, payload.length, true);
    localView.setUint32(22, raw.length, true);
    localView.setUint16(26, nameBytes.length, true);
    localView.setUint16(28, 0, true);            // extra field length
    local.set(nameBytes, 30);

    chunks.push(local, payload);

    const dir = new Uint8Array(46 + nameBytes.length);
    const dirView = new DataView(dir.buffer);
    dirView.setUint32(0, CENTRAL_HEADER_SIG, true);
    dirView.setUint16(4, 20, true);              // version made by
    dirView.setUint16(6, 20, true);              // version needed
    dirView.setUint16(8, FLAG_UTF8, true);
    dirView.setUint16(10, method, true);
    dirView.setUint16(12, dosTime, true);
    dirView.setUint16(14, dosDate, true);
    dirView.setUint32(16, checksum, true);
    dirView.setUint32(20, payload.length, true);
    dirView.setUint32(24, raw.length, true);
    dirView.setUint16(28, nameBytes.length, true);
    dirView.setUint16(30, 0, true);              // extra field length
    dirView.setUint16(32, 0, true);              // comment length
    dirView.setUint16(34, 0, true);              // disk number start
    dirView.setUint16(36, 0, true);              // internal attributes
    dirView.setUint32(38, 0, true);              // external attributes
    dirView.setUint32(42, offset, true);         // offset of local header
    dir.set(nameBytes, 46);
    central.push(dir);

    offset += local.length + payload.length;
  }

  const centralSize = central.reduce((sum, part) => sum + part.length, 0);

  const eocd = new Uint8Array(22);
  const eocdView = new DataView(eocd.buffer);
  eocdView.setUint32(0, EOCD_SIG, true);
  eocdView.setUint16(4, 0, true);                // this disk number
  eocdView.setUint16(6, 0, true);                // disk with central directory
  eocdView.setUint16(8, central.length, true);   // entries on this disk
  eocdView.setUint16(10, central.length, true);  // total entries
  eocdView.setUint32(12, centralSize, true);
  eocdView.setUint32(16, offset, true);          // central directory offset
  eocdView.setUint16(20, 0, true);               // comment length

  return new Blob([...chunks, ...central, eocd], { type: "application/zip" });
}

/** Hand a Blob to the browser as a download. */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
