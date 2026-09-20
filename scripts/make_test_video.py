#!/usr/bin/env python3
"""Generate a small uncompressed test clip for the VLC Bitrate OSD tools.

No third-party dependencies: the AVI is written by hand as raw DIB frames, so
it plays anywhere VLC plays and needs no encoder, no ffmpeg, no PIL on the
machine that generates it.

Every frame carries a burned-in frame number and timecode plus a sweeping bar.
That makes it useful for more than a smoke test: side-by-side VLC instances
driven by ``vlc_sync.py`` can be checked by eye - if both overlays and both
burned-in timecodes agree, the instances are genuinely in sync rather than
merely started at the same moment.

Usage:
    python make_test_video.py                        # test_video.avi in cwd
    python make_test_video.py -o clip.avi -d 30      # 30 seconds at 30 fps
    python make_test_video.py -o big.avi -W 640 -H 360 -d 60
"""

import argparse
import os
import struct
import sys

# 5x7 bitmap font, one string per row, MSB on the left.
FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    " ": ("00000",) * 7,
}

GLYPH_W, GLYPH_H = 5, 7


def put_pixel(buf, width, height, x, y, bgr):
    """Write one pixel, silently clipping anything outside the frame."""
    if 0 <= x < width and 0 <= y < height:
        off = (y * width + x) * 3
        buf[off:off + 3] = bgr


def fill_rect(buf, width, height, x0, y0, w, h, bgr):
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            put_pixel(buf, width, height, x, y, bgr)


def draw_text(buf, width, height, text, x0, y0, scale, bgr):
    """Blit a string of glyphs, each scaled by ``scale``."""
    cx = x0
    for ch in text:
        rows = FONT.get(ch.upper(), FONT[" "])
        for gy, row in enumerate(rows):
            for gx, bit in enumerate(row):
                if bit == "1":
                    fill_rect(buf, width, height,
                              cx + gx * scale, y0 + gy * scale,
                              scale, scale, bgr)
        cx += (GLYPH_W + 1) * scale
    return cx


def text_width(text, scale):
    return len(text) * (GLYPH_W + 1) * scale - scale


def render_frame(index, width, height, fps):
    """Build one BGR frame: moving background, sweep bar, frame no. + timecode."""
    # Background hue advances with the frame, giving a smooth, unmistakable
    # motion cue even when the frames are viewed as a filmstrip.
    phase = (index * 3) % 256
    bg = bytes((120 + phase // 4, 40 + phase // 8, 60 + phase // 6))

    buf = bytearray(bg * (width * height))

    # A 4px white bar that sweeps left to right, one step per 3 frames or so.
    bar_x = (index * 3) % width
    fill_rect(buf, width, height, bar_x, 0, 4, height, b"\xff\xff\xff")

    # Scale the text to the frame so it stays legible at any resolution.
    scale = max(1, min(width // 100, height // 60))
    assert scale >= 1
    margin = max(4, scale * 4)

    frame_label = "%05d" % index
    secs, rem = divmod(index, fps)
    minutes, secs = divmod(secs, 60)
    timecode = "%02d:%02d.%02d" % (minutes, secs,
                                   int(rem * 100 / fps) if fps else 0)

    text_bg = b"\x00\x00\x00"
    box_h = GLYPH_H * scale * 2 + scale * 3
    box_w = max(text_width(frame_label, scale), text_width(timecode, scale))
    box_w += scale * 4
    fill_rect(buf, width, height, margin - scale * 2, margin - scale * 2,
              min(box_w, width - margin), min(box_h, height - margin),
              text_bg)

    white = b"\xff\xff\xff"
    draw_text(buf, width, height, frame_label, margin, margin, scale, white)
    draw_text(buf, width, height, timecode,
              margin, margin + GLYPH_H * scale + scale * 2, scale,
              b"\x80\xff\x80")

    return bytes(buf)


def write_avi(path, width, height, fps, num_frames, frame_size):
    """Write an uncompressed DIB AVI. Returns the file size in bytes.

    Every frame is the same size, so the container can be streamed out frame by
    frame instead of being buffered - a 1080p minute would otherwise need a
    couple of gigabytes of RAM.

    The file carries an ``idx1`` index. It is not optional in practice: VLC
    refuses to open an AVI without one and reports "Broken or missing Index",
    even though the frames themselves decode perfectly. Offsets in the index
    are relative to the start of the ``movi`` FourCC, so the first entry is 4.
    """
    pad = frame_size & 1                     # chunks are word aligned
    frame_stride = 8 + frame_size + pad
    movi_size = 4 + num_frames * frame_stride      # 'movi' + the frame chunks
    idx1_size = 8 + num_frames * 16                # chunk header + entries

    with open(path, "wb") as f:
        # --- hdrl LIST ---
        hdrl_content = b"hdrl"
        # avih: AVIMAINHEADER, 14 ints = 56 bytes.
        AVIF_HASINDEX = 0x00000010
        AVIF_ISINTERLEAVED = 0x00000100
        avih_data = struct.pack(
            "<IIIIIIIIIIIIII",
            1000000 // fps,   # dwMicroSecPerFrame
            0,                # dwMaxBytesPerSec (unknown for raw video)
            0,                # dwPaddingGranularity
            AVIF_HASINDEX | AVIF_ISINTERLEAVED,   # dwFlags
            num_frames,       # dwTotalFrames
            0,                # dwInitialFrames
            1,                # dwStreams
            frame_size,       # dwSuggestedBufferSize
            width, height,    # dwWidth, dwHeight
            0, 0, 0, 0,       # dwReserved[4]
        )
        hdrl_content += b"avih" + struct.pack("<I", 56) + avih_data

        strl_content = b"strl"
        # strh: AVISTREAMHEADER. The two FourCCs are written separately, so the
        # packed payload below is 48 bytes and the chunk is 8 + 48 = 56.
        strh_payload = struct.pack(
            "<IHHIIIIIIIIHHHH",
            0,                # dwFlags
            0,                # wPriority
            0,                # wLanguage
            0,                # dwInitialFrames
            1,                # dwScale
            fps,              # dwRate -> fps frames per second
            0,                # dwStart
            num_frames,       # dwLength
            frame_size,       # dwSuggestedBufferSize
            0,                # dwQuality
            0,                # dwSampleSize
            0, 0, width, height,   # rcFrame RECT
        )
        strl_content += (b"strh" + struct.pack("<I", 4 + 4 + len(strh_payload))
                         + b"vids" + b"DIB " + strh_payload)

        # strf: BITMAPINFOHEADER, 11 fields = 40 bytes. biHeight is negative,
        # i.e. a top-down DIB, which matches the row order render_frame produces.
        strf_data = struct.pack(
            "<IiiHHIIiiII",
            40,                       # biSize
            width, -height,           # biWidth, biHeight (top-down)
            1, 24,                    # biPlanes, biBitCount
            0,                        # biCompression = BI_RGB
            frame_size,               # biSizeImage
            0, 0,                     # biXPelsPerMeter, biYPelsPerMeter
            0, 0,                     # biClrUsed, biClrImportant
        )
        strl_content += b"strf" + struct.pack("<I", len(strf_data)) + strf_data

        hdrl_content += b"LIST" + struct.pack("<I", len(strl_content)) + strl_content

        # --- movi LIST ---
        # Sizes are computable without rendering anything: 'movi' plus an
        # 8-byte chunk header per frame, and every frame is frame_size bytes.
        riff_size = 4 + (8 + len(hdrl_content)) + (8 + movi_size) + idx1_size

        f.write(b"RIFF" + struct.pack("<I", riff_size) + b"AVI ")
        f.write(b"LIST" + struct.pack("<I", len(hdrl_content)) + hdrl_content)
        f.write(b"LIST" + struct.pack("<I", movi_size) + b"movi")

        for i in range(num_frames):
            f.write(b"00dc" + struct.pack("<I", frame_size))
            f.write(render_frame(i, width, height, fps))
            if pad:
                f.write(b"\x00")

        # --- idx1 ---
        # Offsets are relative to the 'movi' FourCC itself, so entry i points
        # at 4 + i * frame_stride: the position of that frame's chunk header.
        # (Verified against an ffmpeg-produced rawvideo AVI, whose first entry
        # is likewise 4.)
        AVIIF_KEYFRAME = 0x00000010
        f.write(b"idx1" + struct.pack("<I", num_frames * 16))
        for i in range(num_frames):
            f.write(b"00dc" + struct.pack("<III", AVIIF_KEYFRAME,
                                          4 + i * frame_stride, frame_size))

    return os.path.getsize(path)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate a raw-DIB AVI test clip with a burned-in "
                    "frame counter and timecode.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("-o", "--output", default="test_video.avi",
                    help="output file path")
    ap.add_argument("-W", "--width", type=int, default=320)
    ap.add_argument("-H", "--height", type=int, default=240)
    ap.add_argument("-f", "--fps", type=int, default=30)
    ap.add_argument("-d", "--duration", type=float, default=4.0,
                    help="clip length in seconds")
    ap.add_argument("--check", action="store_true",
                    help="re-parse the written file and verify its structure")
    args = ap.parse_args(argv)

    if args.width <= 0 or args.height <= 0:
        ap.error("width and height must be positive")
    if args.fps <= 0:
        ap.error("fps must be positive")

    num_frames = max(1, int(round(args.duration * args.fps)))
    frame_size = args.width * args.height * 3

    out = os.path.abspath(args.output)
    parent = os.path.dirname(out)
    if parent and not os.path.isdir(parent):
        sys.exit("error: directory does not exist: %s" % parent)

    size = write_avi(out, args.width, args.height, args.fps, num_frames, frame_size)

    print("wrote %s" % out)
    print("  %dx%d, %d fps, %d frames (%.2fs)"
          % (args.width, args.height, args.fps, num_frames,
             num_frames / float(args.fps)))
    print("  %.1f MB on disk" % (size / 1024.0 / 1024.0))

    if args.check:
        problems = verify_avi(out, num_frames, frame_size)
        if problems:
            for p in problems:
                print("  FAIL %s" % p)
            return 1
        print("  structure OK (RIFF/AVI, %d movi frames of %d bytes, idx1 index)"
              % (num_frames, frame_size))
    return 0


def verify_avi(path, expect_frames, frame_size):
    """Re-read the container and confirm it is laid out as intended.

    Structural only - it does not decode. Playback is a separate question, and
    VLC is the authority there: it refuses outright to open an AVI whose idx1
    index is missing, with "Broken or missing Index", even when every frame
    decodes fine. So the index is checked explicitly rather than assumed.
    """
    with open(path, "rb") as f:
        data = f.read()

    problems = []
    if data[:4] != b"RIFF" or data[8:12] != b"AVI ":
        problems.append("missing RIFF/AVI signature")
        return problems

    riff_size = struct.unpack("<I", data[4:8])[0]
    if riff_size + 8 != len(data):
        problems.append("RIFF size %d does not match file length %d"
                        % (riff_size + 8, len(data)))

    pad = frame_size & 1
    stride = 8 + frame_size + pad

    # avih must advertise the index, or a player may not bother looking for it.
    avih = data.find(b"avih")
    if avih < 0:
        problems.append("no avih chunk")
    else:
        flags = struct.unpack("<I", data[avih + 20:avih + 24])[0]
        if not flags & 0x00000010:      # AVIF_HASINDEX
            problems.append("avih dwFlags lacks AVIF_HASINDEX (0x%08x)" % flags)

    mv = data.find(b"movi")
    if mv < 8:
        problems.append("no movi LIST found")
        return problems

    pos = mv + 4
    found = 0
    while found < expect_frames and pos + 8 <= len(data):
        fourcc = data[pos:pos + 4]
        chunk_size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if fourcc != b"00dc":
            break
        if chunk_size != frame_size:
            problems.append("frame %d has size %d, expected %d"
                            % (found, chunk_size, frame_size))
            break
        pos += stride
        found += 1

    if found != expect_frames:
        problems.append("movi holds %d frames, expected %d" % (found, expect_frames))

    # idx1 must sit immediately after the movi list, and its offsets must
    # actually land on chunk headers.
    movi_list_size = struct.unpack("<I", data[mv - 4:mv])[0]
    ix = mv + movi_list_size
    if data[ix:ix + 4] != b"idx1":
        problems.append("no idx1 chunk directly after the movi list")
        return problems

    entries = struct.unpack("<I", data[ix + 4:ix + 8])[0] // 16
    if entries != expect_frames:
        problems.append("idx1 holds %d entries, expected %d" % (entries, expect_frames))

    for probe in (0, entries // 2, entries - 1):
        if not 0 <= probe < entries:
            continue
        off = struct.unpack("<I", data[ix + 8 + probe * 16 + 8:
                                       ix + 8 + probe * 16 + 12])[0]
        want = 4 + probe * stride
        if off != want:
            problems.append("idx1 entry %d offset is %d, expected %d"
                            % (probe, off, want))
        elif data[mv + off:mv + off + 4] != b"00dc":
            problems.append("idx1 entry %d does not point at a chunk header" % probe)

    return problems


if __name__ == "__main__":
    sys.exit(main())
