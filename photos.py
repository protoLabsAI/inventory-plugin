"""Photo intake — sniff, convert and sanitize an upload so that nothing but pixels (and the
one EXIF field needed to show them upright) is ever stored, let alone published.

A phone photo carries GPS coordinates, the camera serial and the time it was taken; the
catalog is public, so metadata is stripped at the door rather than at publish time.
Keep-lists, not blocklists: a JPEG keeps only the segments a decoder needs (quantisation,
Huffman, frame, scan) plus an ICC colour profile and a bare Adobe colour marker — unusual
markers are refused, not passed on; a PNG keeps only its image chunks (the ICC profile's
free-text name is replaced); a still WebP only its image and colour chunks.
Everything after a JPEG's end-of-image marker (the extra images an iPhone appends) goes.

Pure Python on purpose: Pillow is not guaranteed in the frozen desktop runtime. HEIC/HEIF
is converted with macOS ``sips`` when it is on PATH, else refused with a clear message.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib

#: Upload cap. A full-resolution phone JPEG is 3–8 MB; a HEIC converts to about that.
MAX_BYTES = 20 * 1024 * 1024

CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class PhotoError(ValueError):
    """An upload we will not store, phrased for the operator."""


# ── sniffing ───────────────────────────────────────────────────────────────────
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def sniff(data: bytes) -> str | None:
    """``"jpeg" | "png" | "webp" | "heic"`` from the magic bytes, else ``None`` — the
    filename and the declared content type are never trusted."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == _PNG_SIG:
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 16 and data[4:8] == b"ftyp":
        size = struct.unpack(">I", data[:4])[0]
        brands = {data[8:12]}
        compat = data[16 : max(16, min(size, len(data), 128))]
        brands |= {compat[i : i + 4] for i in range(0, len(compat) - 3, 4)}
        if brands & _HEIF_BRANDS:
            return "heic"
    return None


# ── JPEG ───────────────────────────────────────────────────────────────────────
_SOI, _EOI, _SOS = 0xD8, 0xD9, 0xDA
_RST = set(range(0xD0, 0xD8))
_ORIENTATION_TAG = 0x0112
#: The only length-carrying segments kept verbatim — what a decoder needs: the frame headers
#: (SOF0–3, 5–7, 9–11, 13–15), Huffman and arithmetic-coding tables (DHT, DAC), quantisation
#: tables (DQT), the restart interval (DRI), the line count (DNL) and each scan header (SOS).
_STRUCTURAL = {
    *range(0xC0, 0xC4),
    *range(0xC5, 0xC8),
    *range(0xC9, 0xCC),
    *range(0xCD, 0xD0),
    0xC4,
    0xCC,
    0xDB,
    0xDC,
    0xDD,
    _SOS,
}
_APPN = set(range(0xE0, 0xF0))
_COM = 0xFE
_FILL_RUN = re.compile(rb"\xff+")
#: Inside entropy-coded data a 0xFF is followed by 0x00 (a stuffed byte), a restart marker or
#: another fill byte; anything else is the next real marker. Searched in C, not a Python loop.
_NEXT_MARKER = re.compile(rb"\xff(?![\x00\xd0-\xd7\xff])")


def _keep_app(marker: int, payload: bytes) -> bool:
    """APP2 ``ICC_PROFILE`` survives: it is the colour profile (Display P3 on an iPhone) needed to
    show the colours right, and it carries no location. So does the Adobe APP14 marker when it is
    exactly the standard 12 bytes (a version and the colour-transform flag CMYK/YCCK files need).
    Every other APPn is dropped: EXIF and XMP (APP1 — a fresh orientation-only EXIF is written
    back), the JFIF header and its thumbnail (APP0), IPTC/Photoshop (APP13), MPF, maker notes."""
    if marker == 0xE2:
        return payload.startswith(b"ICC_PROFILE\x00")
    if marker == 0xEE:
        return len(payload) == 12 and payload.startswith(b"Adobe")
    return False


def header_segments(data: bytes) -> list[tuple[int, bytes]]:
    """``(marker, payload)`` for every length-carrying segment before the first scan — for
    reading the orientation and for tests. Stops quietly at anything malformed."""
    out: list[tuple[int, bytes]] = []
    if data[:2] != b"\xff\xd8":
        return out
    i, n = 2, len(data)
    while i + 4 <= n and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xFF:
            i = _FILL_RUN.match(data, i).end() - 1
            continue
        if marker == 0x01 or marker in _RST:
            i += 2
            continue
        if marker in (_SOS, _EOI):
            break
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if length < 2 or i + 2 + length > n:
            break
        out.append((marker, data[i + 4 : i + 2 + length]))
        i += 2 + length
    return out


def _tiff_ifd0(tiff: bytes) -> tuple[str, list[tuple[int, int, int, bytes]]] | None:
    """Parse IFD0 of a TIFF block: ``(endian, [(tag, type, count, value4)])`` or None."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(e + "H", tiff[2:4])[0] != 42:
        return None
    off = struct.unpack(e + "I", tiff[4:8])[0]
    if off + 2 > len(tiff):
        return None
    count = struct.unpack(e + "H", tiff[off : off + 2])[0]
    entries = []
    for k in range(count):
        p = off + 2 + 12 * k
        if p + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack(e + "HHI", tiff[p : p + 8])
        entries.append((tag, typ, cnt, tiff[p + 8 : p + 12]))
    return e, entries


def exif_ifd0_tags(data: bytes) -> list[int]:
    """The IFD0 tag numbers of every EXIF block in a JPEG's header (0x8825 = GPS pointer)."""
    tags: list[int] = []
    for marker, payload in header_segments(data):
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            parsed = _tiff_ifd0(payload[6:])
            if parsed:
                tags.extend(t for t, *_ in parsed[1])
    return tags


def read_orientation(data: bytes) -> int | None:
    """The EXIF Orientation (1–8) of a JPEG, or None when absent or unreadable."""
    for marker, payload in header_segments(data):
        if marker != 0xE1 or not payload.startswith(b"Exif\x00\x00"):
            continue
        parsed = _tiff_ifd0(payload[6:])
        if not parsed:
            return None
        e, entries = parsed
        for tag, typ, cnt, value in entries:
            if tag == _ORIENTATION_TAG and typ == 3 and cnt == 1:
                v = struct.unpack(e + "H", value[:2])[0]
                return v if 1 <= v <= 8 else None
        return None
    return None


def _orientation_segment(orientation: int) -> bytes:
    """A minimal EXIF APP1 carrying ONLY the Orientation tag (big-endian TIFF, one IFD0 entry)."""
    ifd = struct.pack(">H", 1) + struct.pack(">HHIH2x", _ORIENTATION_TAG, 3, 1, orientation) + struct.pack(">I", 0)
    payload = b"Exif\x00\x00" + b"MM\x00\x2a" + struct.pack(">I", 8) + ifd
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def sanitize_jpeg(data: bytes) -> bytes:
    """Rebuild a JPEG from a keep-list: the structural segments and scans, an ICC profile, a
    bare Adobe marker, and — when the original was rotated — a fresh EXIF block carrying only
    the Orientation, written right after SOI. Every APPn/COM beyond that is dropped; a file
    using any other marker (hierarchical, JPEG-LS, reserved) is refused rather than passed on.
    Anything after the end-of-image marker (an iPhone's appended second image) goes."""
    if data[:2] != b"\xff\xd8":
        raise PhotoError("not a JPEG")
    orientation = read_orientation(data)
    out = bytearray(b"\xff\xd8")
    if orientation and orientation != 1:
        out += _orientation_segment(orientation)
    seen_scan = False
    i, n = 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            raise PhotoError(f"corrupt JPEG (expected a marker at byte {i})")
        if i + 1 >= n:
            break
        marker = data[i + 1]
        if marker == 0xFF:  # a run of fill bytes before the marker
            i = _FILL_RUN.match(data, i).end() - 1
            continue
        if marker == _EOI:
            if not seen_scan:
                raise PhotoError("corrupt JPEG (no image data)")
            out += b"\xff\xd9"
            return bytes(out)
        if marker == 0x01 or marker in _RST:  # TEM or a stray restart outside a scan: carries nothing
            i += 2
            continue
        if i + 4 > n:
            break
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if length < 2 or i + 2 + length > n:
            raise PhotoError("corrupt JPEG (a segment runs past the end of the file)")
        segment, payload = data[i : i + 2 + length], data[i + 4 : i + 2 + length]
        i += 2 + length
        if marker in _APPN:
            if _keep_app(marker, payload):
                out += segment
            continue
        if marker == _COM:
            continue
        if marker not in _STRUCTURAL:
            raise PhotoError(
                f"this JPEG uses an unusual encoding (marker 0xFF{marker:02X}) — re-save it as a standard JPEG and upload that"
            )
        out += segment
        if marker == _SOS:
            seen_scan = True
            m = _NEXT_MARKER.search(data, i)
            j = m.start() if m else n
            out += data[i:j]
            i = j
    raise PhotoError("truncated JPEG (no end-of-image marker) — the upload may have been cut off")


# ── PNG ────────────────────────────────────────────────────────────────────────
#: Image and colour chunks only (APNG's animation chunks included); text, EXIF, timestamps,
#: suggested palettes (sPLT, which carries a free-text name) and private chunks are dropped.
_PNG_KEEP = {
    b"IHDR",
    b"PLTE",
    b"IDAT",
    b"IEND",
    b"tRNS",
    b"gAMA",
    b"cHRM",
    b"sRGB",
    b"iCCP",
    b"sBIT",
    b"bKGD",
    b"pHYs",
    b"acTL",
    b"fcTL",
    b"fdAT",
}


def _png_chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def sanitize_png(data: bytes) -> bytes:
    if data[:8] != _PNG_SIG:
        raise PhotoError("not a PNG")
    out = bytearray(_PNG_SIG)
    i, n = 8, len(data)
    while i + 12 <= n:
        length = struct.unpack(">I", data[i : i + 4])[0]
        ctype = data[i + 4 : i + 8]
        end = i + 12 + length
        if end > n:
            break
        if ctype == b"iCCP":
            # The profile's free-text name can say anything; the profile itself is colour data.
            body = data[i + 8 : i + 8 + length]
            k = body.find(b"\x00")
            if 1 <= k <= 79:
                out += _png_chunk(b"iCCP", b"ICC profile" + body[k:])
        elif ctype in _PNG_KEEP:
            out += data[i:end]
        i = end
        if ctype == b"IEND":
            return bytes(out)  # trailing bytes after IEND are dropped
    raise PhotoError("truncated PNG (no IEND chunk) — the upload may have been cut off")


# ── WebP ───────────────────────────────────────────────────────────────────────
_WEBP_KEEP = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ICCP"}
_VP8X_ANIM, _VP8X_EXIF, _VP8X_XMP = 0x02, 0x08, 0x04
_ANIMATED = "animated WebP isn't supported — upload a still photo (JPEG, PNG or a still WebP)"


def sanitize_webp(data: bytes) -> bytes:
    """Keep the image, alpha and colour chunks; drop EXIF and XMP and clear their flags.
    Animated WebP is refused: its frames can nest chunks of their own."""
    if not (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise PhotoError("not a WebP")
    end = min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
    chunks: list[bytes] = []
    i = 12
    while i + 8 <= end:
        fourcc = data[i : i + 4]
        size = struct.unpack("<I", data[i + 4 : i + 8])[0]
        body_end = i + 8 + size
        if body_end > end:
            raise PhotoError("truncated WebP — the upload may have been cut off")
        if fourcc in (b"ANIM", b"ANMF"):
            raise PhotoError(_ANIMATED)
        chunk = bytearray(data[i:body_end])
        if size & 1:
            chunk += b"\x00"  # RIFF pads odd chunks to an even length
        i = body_end + (size & 1)
        if fourcc not in _WEBP_KEEP:
            continue
        if fourcc == b"VP8X" and size >= 1:
            if chunk[8] & _VP8X_ANIM:
                raise PhotoError(_ANIMATED)
            chunk[8] &= ~(_VP8X_EXIF | _VP8X_XMP) & 0xFF
        chunks.append(bytes(chunk))
    if not any(c[:4] in (b"VP8 ", b"VP8L") for c in chunks):
        raise PhotoError("WebP has no image data")
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ── HEIC ───────────────────────────────────────────────────────────────────────
def heic_to_jpeg(data: bytes) -> bytes:
    """Convert HEIC/HEIF (the iPhone default) to JPEG with macOS ``sips``."""
    sips = shutil.which("sips")
    if not sips:
        raise PhotoError(
            "HEIC/HEIF photos can't be converted on this machine — export the photo as JPEG and upload that"
        )
    with tempfile.TemporaryDirectory(prefix="inventory-heic-") as d:
        src, dst = os.path.join(d, "in.heic"), os.path.join(d, "out.jpg")
        with open(src, "wb") as f:
            f.write(data)
        try:
            proc = subprocess.run([sips, "-s", "format", "jpeg", src, "--out", dst], capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PhotoError(f"could not convert the HEIC photo to JPEG: {exc}") from exc
        if proc.returncode != 0 or not os.path.exists(dst):
            detail = (proc.stderr or b"").decode(errors="replace").strip()[-200:] or f"sips exited {proc.returncode}"
            raise PhotoError(f"could not convert the HEIC photo to JPEG: {detail}")
        with open(dst, "rb") as f:
            return f.read()


def prepare_photo(data: bytes) -> tuple[bytes, str]:
    """An upload → ``(sanitized bytes, ext)`` with ext in jpg/png/webp, or PhotoError."""
    if not data:
        raise PhotoError("the upload is empty")
    if len(data) > MAX_BYTES:
        raise PhotoError(
            f"photos are capped at {MAX_BYTES // (1024 * 1024)} MB; this one is {len(data) / 1048576:.1f} MB"
        )
    kind = sniff(data)
    if kind == "heic":
        data = heic_to_jpeg(data)
        kind = sniff(data)
        if kind != "jpeg":
            raise PhotoError("the HEIC conversion did not produce a JPEG")
    if kind == "jpeg":
        return sanitize_jpeg(data), "jpg"
    if kind == "png":
        return sanitize_png(data), "png"
    if kind == "webp":
        return sanitize_webp(data), "webp"
    raise PhotoError("unsupported image — upload a JPEG, PNG, WebP or HEIC photo")
