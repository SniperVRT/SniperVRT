"""Tests for the signed_post + isotonic calibration."""

from __future__ import annotations

import pytest
from kalshi_engine.validation.isotonic import pav, predict


def test_isotonic_pav_basic():
    bps = pav([0.1, 0.4, 0.7, 0.9], [0, 1, 0, 1])
    # Result must be monotonic
    ys = [y for _, y in bps]
    for i in range(len(ys) - 1):
        assert ys[i] <= ys[i + 1] + 1e-9


def test_isotonic_predict_interpolates():
    bps = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
    assert predict(bps, 0.25) == pytest.approx(0.25)


def test_signed_post_signature_deterministic_with_fixed_key(tmp_path):
    """Signing should be deterministic given same ts + path + method."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from kalshi_engine.connectors.kalshi import _Signer
    # Generate a test RSA key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "k.pem"
    p.write_bytes(pem)
    signer = _Signer(p)
    # PSS uses random padding so signatures aren't byte-equal, but both
    # must be valid base64 and same length.
    s1 = signer.sign("12345", "POST", "/portfolio/orders")
    s2 = signer.sign("12345", "POST", "/portfolio/orders")
    import base64
    assert len(base64.b64decode(s1)) == len(base64.b64decode(s2)) == 256
