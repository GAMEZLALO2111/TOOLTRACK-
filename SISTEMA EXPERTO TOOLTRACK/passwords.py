import hashlib
import hmac
import secrets
from typing import Optional

ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), ITERATIONS)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def secrets_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_password(raw_password: str, stored_password: Optional[str]) -> bool:
    if not stored_password:
        return False
    if stored_password.startswith("pbkdf2_sha256$"):
        try:
            _, salt, digest_hex = stored_password.split("$", 2)
            expected = hashlib.pbkdf2_hmac(
                "sha256", raw_password.encode("utf-8"), salt.encode("utf-8"), ITERATIONS
            ).hex()
            return secrets_compare(expected, digest_hex)
        except ValueError:
            return False
    return secrets_compare(stored_password, raw_password)


def ensure_password_hash(password: str) -> str:
    return hash_password(password)
