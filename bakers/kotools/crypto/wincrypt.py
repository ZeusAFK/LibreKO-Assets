"""MS CryptDeriveKey-compatible RC4 cipher for Knight Online encrypted files.

Replicates the client's WinCrypt routine:
  - Provider: MS_ENHANCED_PROV (PROV_RSA_FULL)
  - Hash: SHA-1 of key bytes
  - Key derivation: CryptDeriveKey(CALG_RC4, hash, 0x800000) -> 128-bit RC4 key
  - The 0x800000 flag means key length = 128 bits (upper 16 bits = 0x0080)

For CryptDeriveKey with SHA-1 producing a 128-bit key:
  - The derived key is the first 16 bytes of SHA-1(key_string)
  - SHA-1 produces 20 bytes, we take the first 16

Used for both HDR/SRC archive encryption and NTF v7 texture encryption.
"""

import hashlib


# The cipher string every client build shares.
# May differ for retail — support custom key override.
DEFAULT_KEY = b"owsd9012%$1as!wpow1033b%!@%12"


class RC4:
    """Pure Python RC4 stream cipher."""

    __slots__ = ("_S", "_i", "_j")

    def __init__(self, key: bytes):
        S = list(range(256))
        j = 0
        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) & 0xFF
            S[i], S[j] = S[j], S[i]
        self._S = S
        self._i = 0
        self._j = 0

    def process(self, data: bytes) -> bytes:
        S = self._S
        i, j = self._i, self._j
        out = bytearray(len(data))
        for k in range(len(data)):
            i = (i + 1) & 0xFF
            j = (j + S[i]) & 0xFF
            S[i], S[j] = S[j], S[i]
            out[k] = data[k] ^ S[(S[i] + S[j]) & 0xFF]
        self._i = i
        self._j = j
        return bytes(out)


class KOCipher:
    """Knight Online RC4 cipher matching the client's CryptoAPI usage.

    Each instance is a fresh RC4 stream. Create a new instance per file/texture.
    """

    def __init__(self, key: bytes = DEFAULT_KEY):
        # CryptDeriveKey: SHA-1 hash of key → first 16 bytes → RC4 key
        sha1 = hashlib.sha1(key).digest()
        rc4_key = sha1[:16]  # 128-bit key
        self._rc4 = RC4(rc4_key)

    def decrypt(self, data: bytes) -> bytes:
        return self._rc4.process(data)

    def encrypt(self, data: bytes) -> bytes:
        # RC4 is symmetric: encrypt == decrypt
        return self._rc4.process(data)


def create_cipher(key: bytes = DEFAULT_KEY) -> KOCipher:
    """Create a new KOCipher instance. Use one per file."""
    return KOCipher(key)
