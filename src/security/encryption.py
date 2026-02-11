"""Fernet-based symmetric encryption for sensitive credentials.

Provides helpers to encrypt/decrypt arbitrary dictionaries (API keys,
OAuth tokens, etc.) and a masking utility for safe display in logs and
dashboards.

The encryption key is read from ``Settings.ENCRYPTION_KEY``.  If the value
is already a valid 32-byte URL-safe-base64 Fernet key it is used directly;
otherwise a key is derived from the string via PBKDF2-HMAC-SHA256 so that
plain-text passphrases work out of the box in development.

Usage::

    from src.security.encryption import encrypt_dict, decrypt_dict, mask_value

    token = encrypt_dict({"api_key": "sk-live-abc123"})
    data  = decrypt_dict(token)
    safe  = mask_value(data["api_key"])  # "********c123"
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from typing import Any

import structlog
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = structlog.get_logger(__name__)

# Fixed salt for PBKDF2 derivation.  Changing this value will invalidate
# every token encrypted with a derived key, so treat it as immutable.
_PBKDF2_SALT = b"mas-credential-encryption-salt-v1"
_PBKDF2_ITERATIONS = 480_000


def _is_valid_fernet_key(raw: str) -> bool:
    """Return ``True`` if *raw* is a valid 32-byte URL-safe-base64 Fernet key."""
    try:
        decoded = base64.urlsafe_b64decode(raw)
        return len(decoded) == 32
    except Exception:  # noqa: BLE001
        return False


def _derive_key(passphrase: str) -> bytes:
    """Derive a Fernet-compatible key from an arbitrary passphrase via PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    raw_key = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw_key)


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    """Return a cached :class:`~cryptography.fernet.Fernet` instance.

    The key is sourced from ``Settings().ENCRYPTION_KEY``.  If the value is
    already a valid Fernet key (32 bytes, URL-safe base64) it is used as-is.
    Otherwise it is treated as a passphrase and a key is derived using
    PBKDF2-HMAC-SHA256.

    Returns:
        A ready-to-use ``Fernet`` instance.
    """
    from src.core.config import get_settings

    raw_key = get_settings().ENCRYPTION_KEY

    if _is_valid_fernet_key(raw_key):
        logger.debug("encryption.init", method="direct_key")
        return Fernet(raw_key.encode("utf-8"))

    logger.debug("encryption.init", method="pbkdf2_derived")
    derived = _derive_key(raw_key)
    return Fernet(derived)


def encrypt_dict(data: dict[str, Any]) -> str:
    """Serialize *data* to JSON, encrypt it, and return a URL-safe base64 string.

    Args:
        data: Arbitrary JSON-serializable dictionary.

    Returns:
        An opaque encrypted token (``str``).

    Raises:
        TypeError: If *data* is not JSON-serializable.
    """
    fernet = get_fernet()
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encrypted = fernet.encrypt(payload)
    logger.debug("encryption.encrypt_dict", keys=list(data.keys()))
    return encrypted.decode("utf-8")


def decrypt_dict(token: str) -> dict[str, Any]:
    """Decrypt *token* produced by :func:`encrypt_dict` and return the original dict.

    Args:
        token: The encrypted string returned by :func:`encrypt_dict`.

    Returns:
        The original dictionary.

    Raises:
        cryptography.fernet.InvalidToken: If the token is invalid or tampered.
        json.JSONDecodeError: If the decrypted payload is not valid JSON.
    """
    fernet = get_fernet()
    try:
        decrypted = fernet.decrypt(token.encode("utf-8"))
    except InvalidToken:
        logger.warning("encryption.decrypt_dict.failed", reason="invalid_token")
        raise
    result: dict[str, Any] = json.loads(decrypted)
    logger.debug("encryption.decrypt_dict", keys=list(result.keys()))
    return result


def mask_value(value: str, visible: int = 4) -> str:
    """Mask a sensitive string, leaving only the last *visible* characters.

    Examples::

        >>> mask_value("sk-live-abc123xyz")
        '*************3xyz'
        >>> mask_value("short", visible=10)
        'short'
        >>> mask_value("")
        '****'

    Args:
        value: The sensitive string to mask.
        visible: Number of trailing characters to leave visible.  Defaults to 4.

    Returns:
        A masked string where leading characters are replaced with ``*``.
        If the string is shorter than or equal to *visible*, it is returned
        unchanged (masking would reveal nothing extra).  Empty strings
        return ``'****'`` as a placeholder.
    """
    if not value:
        return "****"
    if len(value) <= visible:
        return value
    masked_count = len(value) - visible
    return "*" * masked_count + value[-visible:]


def mask_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with all string values masked.

    Non-string values are replaced with ``"****"``.
    Nested dicts are masked recursively.

    Args:
        data: Dictionary whose values should be masked.

    Returns:
        A new dictionary safe for API responses.
    """
    masked: dict[str, Any] = {}
    for key, val in data.items():
        if isinstance(val, dict):
            masked[key] = mask_dict(val)
        elif isinstance(val, str):
            masked[key] = mask_value(val)
        else:
            masked[key] = "****"
    return masked


def is_encrypted(credentials: dict[str, Any]) -> bool:
    """Check whether a credentials dict is stored in encrypted form.

    Encrypted credentials use the convention ``{"_encrypted": "<token>"}``.
    """
    return "_encrypted" in credentials and len(credentials) == 1


def encrypt_credentials(credentials: dict[str, Any]) -> dict[str, str]:
    """Encrypt a plain credentials dict into the storage format.

    Returns:
        ``{"_encrypted": "<fernet-token>"}``
    """
    token = encrypt_dict(credentials)
    return {"_encrypted": token}


def decrypt_credentials(credentials: dict[str, Any]) -> dict[str, Any]:
    """Decrypt credentials from storage format to the original dict.

    If the credentials are not in encrypted form, returns them as-is.

    Raises:
        cryptography.fernet.InvalidToken: If the encryption key does not match.
    """
    if is_encrypted(credentials):
        return decrypt_dict(credentials["_encrypted"])
    return credentials
