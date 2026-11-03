"""Photo intake: sniffing, the metadata strippers (hand-built fixtures — no Pillow in the
runtime, none in the suite). The store side lives in tests/test_scans.py."""

from __future__ import annotations

import struct
import zlib

import pytest
from inventory_plugin import photos as ph


# ── fixture builders ───────────────────────────────────────────────────────────
def seg(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def exif_app1(orientation: int | None = 6, gps: bool = True, le: bool = False) -> bytes:
    """An EXIF APP1 with Make, Orientation and a GPS IFD (GPSLatitudeRef 'N')."""
    e = "<" if le else ">"
    entries = [(0x010F, 2, 4, b"Cam\x00")]
    if orientation is not None:
        entries.append((0x0112, 3, 1, struct.pack(e + "H", orientation) + b"\x00\x00"))
    n = len(entries) + (1 if gps else 0)
    gps_off = 8 + 2 + 12 * n + 4
    if gps:
        entries.append((0x8825, 4, 1, struct.pack(e + "I", gps_off)))
    ifd = (
        struct.pack(e + "H", len(entries))
        + b"".join(struct.pack(e + "HHI", t, ty, c) + v for t, ty, c, v in entries)
        + struct.pack(e + "I", 0)
    )
    gps_ifd = (
        (struct.pack(e + "H", 1) + struct.pack(e + "HHI", 0x0001, 2, 2) + b"N\x00\x00\x00" + struct.pack(e + "I", 0))
        if gps
        else b""
    )
    tiff = (b"II" if le else b"MM") + struct.pack(e + "H", 42) + struct.pack(e + "I", 8) + ifd + gps_ifd
    return seg(0xE1, b"Exif\x00\x00" + tiff)


SCAN = b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"  # a stuffed byte and a restart marker inside the scan
FRAME = [
    seg(0xDB, b"\x00" + bytes(64)),
    seg(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00"),
    seg(0xC4, b"\x00" + bytes(16) + b"\x00"),
]


def jpeg(orientation: int | None = 6, gps: bool = True, extras: bool = True, trailer: bool = True, le=False) -> bytes:
    parts = [b"\xff\xd8", seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"), exif_app1(orientation, gps, le)]
    if extras:
        parts += [
            seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><exif:GPSLatitude>45,31N</exif:GPSLatitude>"),
            seg(0xED, b"Photoshop 3.0\x008BIM iptc-city-Portland"),
            seg(0xE2, b"ICC_PROFILE\x00\x01\x01profile-bytes"),
            seg(0xE2, b"MPF\x00multi-picture"),
            seg(0xFE, b"shot at 45.5N 122.6W"),
        ]
    parts += [*FRAME, seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"), SCAN, b"\xff\xd9"]
    if trailer:  # the second image an iPhone appends after the first EOI, with its own EXIF
        parts.append(b"\xff\xd8" + exif_app1(1, True) + b"\xff\xd9")
    return b"".join(parts)


def png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)


def png_file() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + png_chunk(b"tEXt", b"Comment\x00at home")
        + png_chunk(b"eXIf", b"MM\x00*GPS")
        + png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00GPS")
        + png_chunk(b"zTXt", b"k\x00\x00x")
        + png_chunk(b"tIME", b"\x07\xea\x09\x0d\x0c\x00\x00")
        + png_chunk(b"prVt", b"private")
        + png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + png_chunk(b"IEND", b"")
        + b"trailing junk GPS"
    )


def riff_chunk(fourcc: bytes, data: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")


def webp_file() -> bytes:
    vp8x = bytes([0x20 | 0x08 | 0x04, 0, 0, 0]) + b"\x00\x00\x00\x00\x00\x00"
    body = (
        b"WEBP"
        + riff_chunk(b"VP8X", vp8x)
        + riff_chunk(b"ICCP", b"icc")
        + riff_chunk(b"VP8 ", b"abc")  # odd length → padded
        + riff_chunk(b"EXIF", b"MM\x00*GPS-here")
        + riff_chunk(b"XMP ", b"<x:xmpmeta>GPS</x:xmpmeta>")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    out, i = [], 12
    while i + 8 <= len(data):
        size = struct.unpack("<I", data[i + 4 : i + 8])[0]
        out.append((data[i : i + 4], data[i + 8 : i + 8 + size]))
        i += 8 + size + (size & 1)
    return out


def png_types(data: bytes) -> list[bytes]:
    out, i = [], 8
    while i + 12 <= len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        ctype, body, crc = data[i + 4 : i + 8], data[i + 8 : i + 8 + length], data[i + 8 + length : i + 12 + length]
        assert struct.unpack(">I", crc)[0] == zlib.crc32(ctype + body) & 0xFFFFFFFF
        out.append(ctype)
        i += 12 + length
    return out


# ── sniffing ───────────────────────────────────────────────────────────────────
def test_sniff_reads_the_bytes_not_the_name():
    assert ph.sniff(jpeg()) == "jpeg"
    assert ph.sniff(png_file()) == "png"
    assert ph.sniff(webp_file()) == "webp"
    assert ph.sniff(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic") == "heic"
    assert ph.sniff(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42") is None  # a video is not a photo
    assert ph.sniff(b"GIF89a....") is None
    assert ph.sniff(b"") is None


# ── JPEG ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("le", [False, True])
def test_jpeg_loses_gps_and_every_other_tag_but_keeps_orientation_6(le):
    src = jpeg(orientation=6, le=le)
    assert ph.read_orientation(src) == 6 and 0x8825 in ph.exif_ifd0_tags(src)
    out = ph.sanitize_jpeg(src)
    assert ph.read_orientation(out) == 6
    assert ph.exif_ifd0_tags(out) == [0x0112]  # ONLY orientation: no GPS pointer, no Make
    assert out.count(b"Exif\x00\x00") == 1
    for needle in (b"45,31N", b"Portland", b"shot at", b"xmpmeta", b"MPF\x00", b"Cam\x00", b"8BIM"):
        assert needle not in out, needle
    assert b"ICC_PROFILE" in out and b"JFIF" not in out  # colour kept; the JFIF header (and any thumbnail) goes
    assert [m for m, _ in ph.header_segments(out)][:2] == [0xE1, 0xE2]  # orientation first, right after SOI
    assert SCAN in out  # stuffed bytes and restart markers inside the scan survive verbatim
    assert out.endswith(b"\xff\xd9") and out.count(b"\xff\xd8") == 1  # the appended second image is gone


def test_jpeg_without_rotation_keeps_no_exif_at_all():
    for src in (jpeg(orientation=1), jpeg(orientation=None), jpeg(orientation=None, gps=False, extras=False)):
        out = ph.sanitize_jpeg(src)
        assert b"Exif" not in out and ph.read_orientation(out) is None


def test_jpeg_sanitize_is_idempotent_and_keeps_multi_scan_images():
    once = ph.sanitize_jpeg(jpeg())
    assert ph.sanitize_jpeg(once) == once
    progressive = b"".join(
        [
            b"\xff\xd8",
            *FRAME,
            seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"),
            b"\x01\x02\xff\x00",
            seg(0xC4, b"\x10" + bytes(16) + b"\x00"),  # a table between scans
            seg(0xDA, b"\x01\x01\x00\x00\x3f\x00"),
            b"\x03\x04",
            b"\xff\xd9",
        ]
    )
    out = ph.sanitize_jpeg(progressive)
    # nothing to strip, nothing lost; a bare JFIF header now leads, because a file whose first
    # segment is a table or SOF reads as sizeless to the site's image-size reader
    assert out == b"\xff\xd8" + ph._JFIF_APP0 + progressive[2:]
    assert ph.sanitize_jpeg(out) == out


def test_a_truncated_jpeg_is_refused():
    src = jpeg(trailer=False)
    with pytest.raises(ph.PhotoError, match="truncated|corrupt"):
        ph.sanitize_jpeg(src[:-2])
    with pytest.raises(ph.PhotoError):
        ph.sanitize_jpeg(src[:40])


# ── PNG / WebP ─────────────────────────────────────────────────────────────────
def test_png_keeps_only_image_chunks_with_valid_crcs():
    out = ph.sanitize_png(png_file())
    assert png_types(out) == [b"IHDR", b"IDAT", b"IEND"]
    assert b"GPS" not in out and b"at home" not in out and not out.endswith(b"junk GPS")


def test_webp_drops_exif_and_xmp_and_clears_their_flags():
    out = ph.sanitize_webp(webp_file())
    chunks = webp_chunks(out)
    assert [c for c, _ in chunks] == [b"VP8X", b"ICCP", b"VP8 "]
    assert chunks[0][1][0] == 0x20  # ICC flag kept, EXIF + XMP flags cleared
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8 and len(out) % 2 == 0
    assert b"GPS" not in out and b"xmpmeta" not in out


# ── prepare_photo ──────────────────────────────────────────────────────────────
def test_prepare_refuses_empty_oversized_and_unknown(monkeypatch):
    with pytest.raises(ph.PhotoError, match="empty"):
        ph.prepare_photo(b"")
    monkeypatch.setattr(ph, "MAX_BYTES", 100)
    with pytest.raises(ph.PhotoError, match="capped"):
        ph.prepare_photo(b"\xff\xd8\xff" + bytes(200))
    monkeypatch.setattr(ph, "MAX_BYTES", 20 * 1024 * 1024)
    with pytest.raises(ph.PhotoError, match="unsupported"):
        ph.prepare_photo(b"GIF89a" + bytes(20))
    assert ph.prepare_photo(png_file())[1] == "png" and ph.prepare_photo(webp_file())[1] == "webp"
    data, ext = ph.prepare_photo(jpeg())
    assert ext == "jpg" and ph.exif_ifd0_tags(data) == [0x0112]


def test_heic_without_sips_is_refused_with_a_way_forward(monkeypatch):
    monkeypatch.setattr(ph.shutil, "which", lambda name: None)
    with pytest.raises(ph.PhotoError, match="export the photo as JPEG"):
        ph.prepare_photo(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + bytes(64))
