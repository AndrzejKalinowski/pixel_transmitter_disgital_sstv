"""CC1101 PN9 de-whitening.

The CC1101 whitens everything after the sync word (length byte, packet
body, CRC) by XOR with a PN9 sequence: 9-bit LFSR, polynomial x^9 + x^5 + 1,
seeded with all ones (0x1FF), advanced 8 shifts per byte. rtl_433's flex
decoder does NOT de-whiten, so the receiver applies this to the raw row
bytes. XOR is symmetric, so "dewhiten" == "whiten".
"""


def pn9_bytes(n: int) -> bytes:
    state = 0x1FF
    out = bytearray()
    for _ in range(n):
        out.append(state & 0xFF)
        for _ in range(8):
            feedback = ((state ^ (state >> 5)) & 1) << 8
            state = (state >> 1) | feedback
    return bytes(out)


def dewhiten(data: bytes) -> bytes:
    return bytes(b ^ p for b, p in zip(data, pn9_bytes(len(data))))


# Self-test against the reference PN9 table in TI design note DN509.
_DN509_FIRST_BYTES = bytes.fromhex("ffe11d9aed853324ea")
assert pn9_bytes(9) == _DN509_FIRST_BYTES, "PN9 implementation broken"
