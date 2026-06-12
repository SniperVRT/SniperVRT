"""Tests for Fernet keystore."""

from __future__ import annotations

import pytest
from copy_trade.security.keystore import (
    init_keystore, load_keystore, keystore_exists,
)


def test_round_trip(tmp_path):
    p = tmp_path / "keystore.bin"
    init_keystore("0xdeadbeef", "correct horse battery staple", p)
    assert keystore_exists(p)
    out = load_keystore("correct horse battery staple", p)
    assert out == "0xdeadbeef"


def test_wrong_passphrase(tmp_path):
    p = tmp_path / "keystore.bin"
    init_keystore("0xkey", "right", p)
    with pytest.raises(ValueError, match="wrong passphrase"):
        load_keystore("wrong", p)


def test_keystore_missing(tmp_path):
    p = tmp_path / "missing.bin"
    with pytest.raises(FileNotFoundError):
        load_keystore("any", p)


def test_key_never_in_plaintext(tmp_path):
    """File contents must not contain the private key string."""
    p = tmp_path / "keystore.bin"
    init_keystore("0xSENSITIVE_KEY_VALUE_12345", "pw", p)
    blob = p.read_bytes()
    assert b"SENSITIVE_KEY_VALUE" not in blob
    assert b"0x" not in blob or blob.count(b"0x") < 3  # noise tolerance
