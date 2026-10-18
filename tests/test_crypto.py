import pytest
from cryptography.fernet import Fernet

import app.common.crypto as crypto_module
from app.common.crypto import decrypt_text, encrypt_text


def test_roundtrip(fernet_key):
    assert decrypt_text(encrypt_text("Hello, World!")) == "Hello, World!"


def test_roundtrip_unicode(fernet_key):
    text = "José García — +44 7700 900123"
    assert decrypt_text(encrypt_text(text)) == text


def test_encrypt_produces_different_ciphertexts(fernet_key):
    # Fernet uses a random IV so each call produces a different ciphertext.
    c1 = encrypt_text("same")
    c2 = encrypt_text("same")
    assert c1 != c2


def test_decrypt_wrong_key_raises(fernet_key, monkeypatch):
    cipher = encrypt_text("secret")
    other_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", other_key)
    crypto_module._fernet_instance = None
    with pytest.raises(Exception):
        decrypt_text(cipher)


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("FERNET_KEY", raising=False)
    crypto_module._fernet_instance = None
    with pytest.raises(RuntimeError, match="FERNET_KEY"):
        encrypt_text("x")


def test_instance_is_cached(fernet_key):
    crypto_module._fernet_instance = None
    f1 = crypto_module._fernet()
    f2 = crypto_module._fernet()
    assert f1 is f2
