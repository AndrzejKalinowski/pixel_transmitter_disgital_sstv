#!/usr/bin/env bash
# Receive the pixel_transmitter frames with an RTL-SDR and rebuild out.png.
#
# ==== The flex decoder spec, field by field ====
# rtl_433 knows nothing about our custom packets; the -X string teaches it
# the PHY. It must match firmware/src/radio.cpp + protocol.h exactly:
#
#   n=pixeltx        decoder name; reassemble.py filters JSON on this model
#   m=FSK_PCM        2-FSK, NRZ bit coding (CC1101 packet mode, no Manchester)
#   s=208 l=208      microseconds per bit: 4800 bps -> 208.33 us. short==long
#                    tells rtl_433 the coding is NRZ rather than RZ.
#   r=3000           row reset after 3 ms with no transitions. PN9-whitened
#                    data never runs longer than ~9 identical bits (1.9 ms),
#                    and the firmware pauses 5 ms between packets, so 3 ms
#                    cleanly ends a packet without splitting one.
#   preamble=aad391  alignment pattern: last preamble byte (0xAA) + sync word
#                    0xD3 0x91. rtl_433 strips everything through this, so
#                    each output row starts at the CC1101 length byte.
#   bits>=80         drop shorter junk rows. Smallest real packet (frame
#                    header) is 12 bytes = 96 bits on air; 80 keeps slightly
#                    truncated ones so the CRC check can arbitrate.
#
# NOTE: rtl_433 outputs the rows still PN9-WHITENED and cannot verify the
# CC1101 CRC. De-whitening AND CRC checking both happen in reassemble.py.
#
# Bench tips (CLAUDE.md): the RTL-SDR overloads at desk range — use low gain
# (GAIN env var, e.g. GAIN=1), or pull the SDR antenna entirely.

# DELIBERATELY tuned 25 kHz below the real carrier (~433.985M measured with
# analyze_capture.py) so the FSK tones land at +20.6/+29.8 kHz, clear of the
# RTL-SDR's DC spike — a tone near 0 Hz offset kills rtl_433's FSK detector.
FREQ="${FREQ:-433.960M}"
FLEX='n=pixeltx,m=FSK_PCM,s=208,l=208,r=3000,preamble=aad391,bits>=80'

# Fixed gain by default: auto gain clips on idle noise on this bench
# (+1.5 dBFS idle at auto vs -45 dBFS floor at 20 dB). Override with GAIN.
GAIN="${GAIN:-20}"

rtl_433 -f "$FREQ" -s 250k -g "$GAIN" \
        -X "$FLEX" -F json \
    | python3 "$(dirname "$0")/reassemble.py" --out "$(dirname "$0")/out.png" "$@"
