import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config import settings


def derive_fernet_key(secret_key: str, info: bytes) -> Fernet:
    """Derive a Fernet encryption key from the SECRET_KEY env var.

    Uses HKDF with a static app-scoped salt. The info parameter scopes
    the derived key — different info strings produce different keys.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"greateye-fernet-v1",
        info=info,
    )
    key = hkdf.derive(secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


# Pre-built Fernet instance for provider config encryption
_fernet = derive_fernet_key(settings.secret_key, b"provider-config-encryption")


def encrypt(plaintext: str) -> str:
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted string value."""
    return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
