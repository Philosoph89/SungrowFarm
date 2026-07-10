"""Pure-Python crypto helpers for the iSolarCloud "secured" OpenAPI mode.

Newer developer-portal applications require every request body to be
AES-128-ECB encrypted with a random per-request key; that key is sent
RSA-PKCS#1-v1.5-encrypted in the `x-random-secret-key` header. Responses come
back as an uppercase hex string encrypted with the same AES key.

Implemented without native dependencies (cryptography/pycryptodome need Rust
or C builds that are painful on armv7/musl). Payloads are tiny, so pure-Python
AES speed is irrelevant.
"""
from __future__ import annotations

import base64
import secrets
import string

# ============================== AES-128 =================================

_SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a ^= 0x11b
    return a & 0xff


def _mul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _expand_key(key: bytes) -> list[list[int]]:
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        w = list(words[i - 1])
        if i % 4 == 0:
            w = w[1:] + w[:1]
            w = [_SBOX[b] for b in w]
            w[0] ^= _RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], w)])
    return [sum(words[i:i + 4], []) for i in range(0, 44, 4)]  # 11 round keys


def _add_round_key(s, rk):
    return [b ^ k for b, k in zip(s, rk)]


def _sub_bytes(s, box):
    return [box[b] for b in s]


# state is column-major: s[r + 4c]
def _shift_rows(s):
    return [
        s[0], s[5], s[10], s[15],
        s[4], s[9], s[14], s[3],
        s[8], s[13], s[2], s[7],
        s[12], s[1], s[6], s[11],
    ]


def _inv_shift_rows(s):
    return [
        s[0], s[13], s[10], s[7],
        s[4], s[1], s[14], s[11],
        s[8], s[5], s[2], s[15],
        s[12], s[9], s[6], s[3],
    ]


def _mix_columns(s, inv=False):
    out = []
    for c in range(4):
        col = s[4 * c: 4 * c + 4]
        if not inv:
            m = [(2, 3, 1, 1), (1, 2, 3, 1), (1, 1, 2, 3), (3, 1, 1, 2)]
        else:
            m = [(14, 11, 13, 9), (9, 14, 11, 13), (13, 9, 14, 11), (11, 13, 9, 14)]
        for row in m:
            out.append(_mul(col[0], row[0]) ^ _mul(col[1], row[1]) ^
                       _mul(col[2], row[2]) ^ _mul(col[3], row[3]))
    return out


def _encrypt_block(block: bytes, rks) -> bytes:
    s = _add_round_key(list(block), rks[0])
    for rnd in range(1, 10):
        s = _sub_bytes(s, _SBOX)
        s = _shift_rows(s)
        s = _mix_columns(s)
        s = _add_round_key(s, rks[rnd])
    s = _sub_bytes(s, _SBOX)
    s = _shift_rows(s)
    s = _add_round_key(s, rks[10])
    return bytes(s)


def _decrypt_block(block: bytes, rks) -> bytes:
    s = _add_round_key(list(block), rks[10])
    for rnd in range(9, 0, -1):
        s = _inv_shift_rows(s)
        s = _sub_bytes(s, _INV_SBOX)
        s = _add_round_key(s, rks[rnd])
        s = _mix_columns(s, inv=True)
    s = _inv_shift_rows(s)
    s = _sub_bytes(s, _INV_SBOX)
    s = _add_round_key(s, rks[0])
    return bytes(s)


def _aes_key(password: str) -> bytes:
    """Sungrow derives the AES key as the 16-char key, space-padded/truncated."""
    return password.encode("utf-8").ljust(16)[:16]


def aes_encrypt_ecb_hex(plaintext: str, password: str) -> str:
    """AES-128-ECB + PKCS7, returned as uppercase hex (Sungrow wire format)."""
    rks = _expand_key(_aes_key(password))
    data = plaintext.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    out = b"".join(_encrypt_block(data[i:i + 16], rks) for i in range(0, len(data), 16))
    return out.hex().upper()


def aes_decrypt_ecb_hex(hex_ciphertext: str, password: str) -> str:
    rks = _expand_key(_aes_key(password))
    data = bytes.fromhex(hex_ciphertext.strip())
    out = b"".join(_decrypt_block(data[i:i + 16], rks) for i in range(0, len(data), 16))
    pad = out[-1]
    if not 1 <= pad <= 16:
        raise ValueError("bad PKCS7 padding")
    return out[:-pad].decode("utf-8")


# =============================== RSA ====================================

def _der_read(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """Read one TLV; return (tag, value, next_pos)."""
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7f
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    return tag, data[pos:pos + length], pos + length


def parse_rsa_public_key(b64_der: str) -> tuple[int, int]:
    """Parse a base64 X.509 SubjectPublicKeyInfo (as shown in the Sungrow
    developer portal) into (modulus, exponent)."""
    cleaned = "".join(b64_der.split())
    cleaned = cleaned.replace("-----BEGINPUBLICKEY-----", "").replace("-----ENDPUBLICKEY-----", "")
    pad = "=" * (-len(cleaned) % 4)
    try:
        der = base64.urlsafe_b64decode(cleaned + pad)
    except Exception:
        der = base64.b64decode(cleaned + pad)
    _, spki, _ = _der_read(der, 0)                    # SubjectPublicKeyInfo SEQ
    pos = 0
    tag, _alg, pos = _der_read(spki, pos)             # AlgorithmIdentifier SEQ
    tag, bitstr, pos = _der_read(spki, pos)           # BIT STRING
    if tag != 0x03:
        raise ValueError("unexpected DER structure for RSA public key")
    _, rsakey, _ = _der_read(bitstr[1:], 0)           # RSAPublicKey SEQ (skip unused-bits byte)
    pos = 0
    tag, n_bytes, pos = _der_read(rsakey, pos)        # INTEGER n
    tag, e_bytes, pos = _der_read(rsakey, pos)        # INTEGER e
    return int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big")


def rsa_encrypt_pkcs1_b64(data: str, n: int, e: int) -> str:
    """RSA PKCS#1 v1.5 encryption (chunked like Sungrow's Java reference),
    urlsafe-base64 encoded."""
    k = (n.bit_length() + 7) // 8
    max_chunk = k - 11
    raw = data.encode("utf-8")
    out = b""
    for i in range(0, len(raw), max_chunk):
        chunk = raw[i:i + max_chunk]
        ps_len = k - len(chunk) - 3
        ps = bytes(secrets.choice(range(1, 256)) for _ in range(ps_len))
        eb = b"\x00\x02" + ps + b"\x00" + chunk
        c = pow(int.from_bytes(eb, "big"), e, n)
        out += c.to_bytes(k, "big")
    return base64.urlsafe_b64encode(out).decode("ascii")


_ALNUM = string.ascii_letters + string.digits


def random_key(length: int = 16) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(length))


def random_nonce(length: int = 32) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(length))
