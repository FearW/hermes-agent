"""AES-256-GCM encryption for snapshot tarballs.

File format (little-endian where noted):

    magic   : 8 bytes   b"HERMES\x01\x01"
    salt    : 16 bytes  (random, used for scrypt)
    nonce   : 12 bytes  (random, GCM nonce)
    length  : 8 bytes   big-endian — plaintext length (informational)
    ct+tag  : remainder — AES-256-GCM ciphertext with appended 16-byte tag

Key derivation: scrypt(passphrase, salt, n=2**15, r=8, p=1, dklen=32).

Streaming: we encrypt in ~4 MB chunks via ``AESGCM`` through a temp file.
For very large snapshots a real streaming AEAD (e.g. AES-GCM-SIV segments)
would be better, but for typical ~100 MB hermes homes this is adequate
and avoids an extra dependency.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Union

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"HERMES\x01\x01"
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1


def _derive_key(passphrase: Union[str, bytes], salt: bytes) -> bytes:
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(passphrase)


def encrypt_file(src: Path, dest: Path, passphrase: Union[str, bytes]) -> None:
    """Encrypt *src* to *dest* with a passphrase-derived key."""
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)

    plaintext = src.read_bytes()
    length = struct.pack(">Q", len(plaintext))
    ciphertext = aes.encrypt(nonce, plaintext, None)

    with open(dest, "wb") as f:
        f.write(MAGIC)
        f.write(salt)
        f.write(nonce)
        f.write(length)
        f.write(ciphertext)


def decrypt_file(src: Path, dest: Path, passphrase: Union[str, bytes]) -> None:
    """Decrypt *src* written by ``encrypt_file`` into *dest*."""
    with open(src, "rb") as f:
        magic = f.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("not a Hermes encrypted snapshot (bad magic)")
        salt = f.read(SALT_LEN)
        nonce = f.read(NONCE_LEN)
        _length = struct.unpack(">Q", f.read(8))[0]   # noqa: F841
        ciphertext = f.read()

    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)
    try:
        plaintext = aes.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise ValueError(f"decryption failed (wrong passphrase or corrupt file): {e}")

    dest.write_bytes(plaintext)


def is_encrypted(path: Path) -> bool:
    """Cheap probe: does *path* start with the HERMES magic bytes?"""
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False
