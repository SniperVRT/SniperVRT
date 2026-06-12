"""Fernet-encrypted private-key storage.

Private key never written to disk in plaintext; never logged.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEYSTORE_PATH = Path("data/keystore.bin")
SALT_LEN = 16
PBKDF2_ITERS = 390_000


def _derive(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=PBKDF2_ITERS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def init_keystore(private_key: str, passphrase: str,
                  path: Path = KEYSTORE_PATH) -> None:
    """Encrypt and persist. Overwrites if exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_LEN)
    key = _derive(passphrase, salt)
    token = Fernet(key).encrypt(private_key.encode())
    path.write_bytes(salt + token)


def load_keystore(passphrase: str,
                  path: Path = KEYSTORE_PATH) -> str:
    """Decrypt and return private key. Raises on wrong passphrase or missing file."""
    if not path.exists():
        raise FileNotFoundError(f"keystore not found: {path}")
    blob = path.read_bytes()
    if len(blob) < SALT_LEN + 1:
        raise ValueError("keystore corrupt")
    salt, token = blob[:SALT_LEN], blob[SALT_LEN:]
    key = _derive(passphrase, salt)
    try:
        return Fernet(key).decrypt(token).decode()
    except InvalidToken as e:
        raise ValueError("wrong passphrase") from e


def keystore_exists(path: Path = KEYSTORE_PATH) -> bool:
    return path.exists() and path.stat().st_size > SALT_LEN
