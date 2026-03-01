import secrets

import bcrypt


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost factor 12)."""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash. Timing-safe."""
    return bcrypt.checkpw(
        password.encode("utf-8"), password_hash.encode("utf-8")
    )


def generate_session_id() -> str:
    """Generate a cryptographically random session ID (32 bytes, hex-encoded)."""
    return secrets.token_hex(32)
