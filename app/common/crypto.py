import os

from cryptography.fernet import Fernet

_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        key = os.environ.get("FERNET_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing FERNET_KEY env var")
        _fernet_instance = Fernet(key.encode("utf-8"))
    return _fernet_instance


def encrypt_text(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_text(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
