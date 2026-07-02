#!/usr/bin/env python3
"""Rebuild the transmitted image from rtl_433 flex-decoder JSON output.

Usage (live):
    rtl_433 ... -F json | python reassemble.py            (see run_rtl433.*)
Usage (offline replay):
    python reassemble.py capture.jsonl

Pipeline per received row:
  1. rtl_433 aligned on preamble+sync (aad391) and delivers the raw bits
     after it — still PN9-whitened.
  2. De-whiten (dewhiten.py). First byte is the CC1101 length byte.
  3. Verify the CC1101 CRC-16 (crc16_cc1101 over length byte + body). This
     replaces the chip-side CRC filtering an actual CC1101 receiver would
     do — rtl_433 cannot check it because the bits it sees are whitened.
  4. Parse per protocol.py, dedupe by (tile_index, chunk_index), and paint
     every fully-received tile onto the canvas. Missing tiles stay gray.
     Tiles accumulate across repeated transmissions of the same frame.

out.png is rewritten every time a tile completes, so you can watch the
image build up SSTV-style with any auto-reloading viewer (or --show for a
live OpenCV window if opencv-python is installed).
"""

import argparse
import json
import sys
from collections import Counter

import numpy as np
from PIL import Image

import protocol as P
from dewhiten import dewhiten


def rgb565_to_rgb888(payload: bytes) -> np.ndarray:
    """RGB565 little-endian byte pairs -> (N, 3) uint8, with bit replication."""
    px = np.frombuffer(payload, dtype="<u2")
    r = ((px >> 11) & 0x1F).astype(np.uint16)
    g = ((px >> 5) & 0x3F).astype(np.uint16)
    b = (px & 0x1F).astype(np.uint16)
    return np.stack(
        [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1
    ).astype(np.uint8)


class Reassembler:
    def __init__(self, out_path: str, show: bool, verbose: bool):
        self.out_path = out_path
        self.verbose = verbose
        self.stats = Counter()
        # Geometry defaults to the compiled-in protocol so reception works
        # even if all frame-header packets are lost; a received header can
        # override (and re-init) it.
        self.w, self.h = P.TX_W, P.TX_H
        self.tile_px = P.TILE_PIXELS
        self.total_tiles = P.TOTAL_TILES
        self.tiles = {}       # tile_index -> {chunk_index: payload bytes}
        self.done = set()
        self.canvas = self._gray_canvas()

        self.viewer = None
        if show:
            try:
                import cv2  # noqa: F401
                self.viewer = cv2
            except ImportError:
                print("(--show requested but opencv-python not installed; skipping)")

    def _gray_canvas(self) -> np.ndarray:
        return np.full((self.h, self.w, 3), 128, dtype=np.uint8)

    # ---- packet handling ----------------------------------------------

    def process_row(self, hexstr: str) -> None:
        self.stats["rows"] += 1
        if len(hexstr) % 2:
            hexstr += "0"  # rtl_433 pads partial trailing nibbles
        try:
            raw = bytes.fromhex(hexstr)
        except ValueError:
            self.stats["bad_hex"] += 1
            return

        # rtl_433 strips through the preamble pattern, so rows normally
        # start at the whitened length byte — but tolerate a build that
        # keeps the sync word.
        candidates = [raw]
        if raw[:2] == P.SYNC_WORD:
            candidates.insert(0, raw[2:])
        for candidate in candidates:
            if self.process_packet(dewhiten(candidate)):
                return
        self.stats["unparsed"] += 1

    def process_packet(self, data: bytes) -> bool:
        """Validate + dispatch one DE-WHITENED packet, starting at the
        CC1101 length byte. Public: live_rx.py feeds packets in here."""
        if len(data) < 1:
            return False
        plen = data[0]
        if not (P.FRAME_HEADER_BYTES <= plen <= 5 + P.PKT_PAYLOAD_MAX):
            return False
        if len(data) < 1 + plen + 2:
            self.stats["truncated"] += 1
            return False
        body = data[1 : 1 + plen]
        crc_calc = P.crc16_cc1101(data[: 1 + plen])
        crc_hi_lo = (data[1 + plen] << 8) | data[2 + plen]
        crc_lo_hi = (data[2 + plen] << 8) | data[1 + plen]
        if crc_calc == crc_hi_lo:
            self.stats["crc_ok"] += 1
        elif crc_calc == crc_lo_hi:
            self.stats["crc_ok_swapped"] += 1  # byte-order note, still valid
        else:
            self.stats["crc_fail"] += 1
            return False
        if body[0] != P.PKT_MAGIC:
            self.stats["bad_magic"] += 1
            return False

        if body[1] == P.TILE_INDEX_HEADER:
            self._handle_header(body)
        else:
            self._handle_chunk(body)
        return True

    def _handle_header(self, body: bytes) -> None:
        if len(body) < P.FRAME_HEADER_BYTES:
            self.stats["bad_header"] += 1
            return
        w = body[2] | (body[3] << 8)
        h = body[4] | (body[5] << 8)
        tile_px, mode, total = body[6], body[7], body[8]
        self.stats["headers"] += 1
        if mode != P.COLOR_MODE_RGB565:
            print(f"WARNING: unknown color mode {mode}, treating as RGB565")
        if (w, h, tile_px, total) != (self.w, self.h, self.tile_px, self.total_tiles):
            print(f"frame header: geometry {w}x{h}, tile {tile_px}, "
                  f"{total} tiles — re-initializing canvas")
            self.w, self.h, self.tile_px, self.total_tiles = w, h, tile_px, total
            self.tiles.clear()
            self.done.clear()
            self.canvas = self._gray_canvas()
        elif self.stats["headers"] == 1:
            print(f"frame header: geometry {w}x{h}, tile {tile_px}, {total} tiles")

    def _handle_chunk(self, body: bytes) -> None:
        tile, chunk, chunk_count, plen = body[1], body[2], body[3], body[4]
        payload = body[5 : 5 + plen]
        if tile >= self.total_tiles or chunk >= chunk_count or len(payload) != plen:
            self.stats["bad_fields"] += 1
            return
        chunks = self.tiles.setdefault(tile, {})
        if chunk in chunks:
            self.stats["dupes"] += 1
            return
        chunks[chunk] = payload
        self.stats["chunks"] += 1
        if self.verbose:
            print(f"  tile {tile:2d} chunk {chunk}/{chunk_count} "
                  f"({len(chunks)}/{chunk_count} held)")
        if len(chunks) == chunk_count and tile not in self.done:
            self._paint_tile(tile, chunks, chunk_count)

    def _paint_tile(self, tile: int, chunks: dict, chunk_count: int) -> None:
        blob = b"".join(chunks[i] for i in range(chunk_count))
        tile_bytes = self.tile_px * self.tile_px * 2
        if len(blob) != tile_bytes:
            self.stats["bad_tile_size"] += 1
            return
        rgb = rgb565_to_rgb888(blob).reshape(self.tile_px, self.tile_px, 3)
        tiles_x = self.w // self.tile_px
        x0 = (tile % tiles_x) * self.tile_px
        y0 = (tile // tiles_x) * self.tile_px
        self.canvas[y0 : y0 + self.tile_px, x0 : x0 + self.tile_px] = rgb
        self.done.add(tile)
        print(f"tile {tile:2d} complete -> {len(self.done)}/{self.total_tiles} tiles")
        self.save()
        self._update_view()

    # ---- output ---------------------------------------------------------

    def save(self) -> None:
        Image.fromarray(self.canvas).save(self.out_path)

    def _update_view(self) -> None:
        if self.viewer is None:
            return
        cv2 = self.viewer
        big = cv2.resize(self.canvas[:, :, ::-1], (self.w * 4, self.h * 4),
                         interpolation=cv2.INTER_NEAREST)
        cv2.imshow("pixel_transmitter RX", big)
        cv2.waitKey(1)

    def summary(self) -> None:
        print("\n--- RX summary ---")
        for key in ("rows", "crc_ok", "crc_ok_swapped", "crc_fail", "unparsed",
                    "truncated", "headers", "chunks", "dupes", "bad_magic",
                    "bad_fields", "bad_tile_size"):
            if self.stats[key]:
                print(f"  {key:16s} {self.stats[key]}")
        print(f"  tiles complete   {len(self.done)}/{self.total_tiles}")
        print(f"  saved            {self.out_path}")


def rows_from_json_line(line: str, model: str):
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return
    if obj.get("model") != model:
        return
    if isinstance(obj.get("rows"), list):
        for row in obj["rows"]:
            if "data" in row:
                yield row["data"]
    elif isinstance(obj.get("codes"), list):
        for code in obj["codes"]:  # "{480}aabbcc..."
            if code.startswith("{") and "}" in code:
                yield code.split("}", 1)[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", default="-",
                    help="rtl_433 JSON-lines file, or - for stdin (default)")
    ap.add_argument("--out", default="out.png", help="output image path")
    ap.add_argument("--model", default="pixeltx",
                    help="flex decoder name to accept (matches n= in the -X spec)")
    ap.add_argument("--show", action="store_true", help="live OpenCV window")
    ap.add_argument("-v", "--verbose", action="store_true", help="per-chunk logging")
    args = ap.parse_args()

    rx = Reassembler(args.out, args.show, args.verbose)
    rx.save()  # gray canvas exists from second zero
    stream = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8")
    print(f"listening (model '{args.model}') — Ctrl-C to stop")
    try:
        for line in stream:
            for hexdata in rows_from_json_line(line, args.model):
                rx.process_row(hexdata)
    except KeyboardInterrupt:
        pass
    finally:
        rx.save()
        rx.summary()


if __name__ == "__main__":
    main()
