"""Unit tests for the Fernet-based encryption utility.

Tests the ``src.security.encryption`` module: key derivation, round-trip
encrypt/decrypt for dicts and credentials, masking helpers, and error paths.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet, InvalidToken

from src.security.encryption import (
    _derive_key,
    _is_valid_fernet_key,
    decrypt_credentials,
    decrypt_dict,
    encrypt_credentials,
    encrypt_dict,
    get_fernet,
    is_encrypted,
    mask_dict,
    mask_value,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_PASSPHRASE = "test-encryption-key-12345"  # noqa: S105


def _make_mock_settings(encryption_key: str = _TEST_PASSPHRASE) -> MagicMock:
    """Build a mock Settings object with the given ENCRYPTION_KEY."""
    settings = MagicMock()
    settings.ENCRYPTION_KEY = encryption_key
    return settings


def _generate_valid_fernet_key() -> str:
    """Generate a real valid 32-byte URL-safe base64 Fernet key."""
    return Fernet.generate_key().decode("utf-8")


@pytest.fixture(autouse=True)
def _clear_fernet_cache():
    """Clear the lru_cache on get_fernet before and after each test."""
    get_fernet.cache_clear()
    yield
    get_fernet.cache_clear()


# ---------------------------------------------------------------------------
# _is_valid_fernet_key
# ---------------------------------------------------------------------------


class TestIsValidFernetKey:
    """Tests for the _is_valid_fernet_key helper."""

    def test_valid_fernet_key_returns_true(self) -> None:
        """A properly generated Fernet key should be recognised as valid."""
        key = _generate_valid_fernet_key()
        assert _is_valid_fernet_key(key) is True

    def test_arbitrary_passphrase_returns_false(self) -> None:
        """A plain-text passphrase is not a valid Fernet key."""
        assert _is_valid_fernet_key("my-secret-passphrase") is False  # noqa: S106

    def test_empty_string_returns_false(self) -> None:
        """An empty string is not a valid Fernet key."""
        assert _is_valid_fernet_key("") is False

    def test_base64_but_wrong_length_returns_false(self) -> None:
        """A base64 string of the wrong byte length should fail."""
        short_key = base64.urlsafe_b64encode(b"0123456789abcdef").decode()  # 16 bytes
        assert _is_valid_fernet_key(short_key) is False

    def test_non_base64_string_returns_false(self) -> None:
        """Non-base64 garbage should return False, not raise."""
        assert _is_valid_fernet_key("!!!not-base64!!!") is False


# ---------------------------------------------------------------------------
# _derive_key
# ---------------------------------------------------------------------------


class TestDeriveKey:
    """Tests for the PBKDF2 key derivation helper."""

    def test_returns_url_safe_base64_bytes(self) -> None:
        """Derived key should be URL-safe base64 encoded bytes."""
        derived = _derive_key("some-passphrase")  # noqa: S106
        assert isinstance(derived, bytes)
        # Should be decodable as base64
        decoded = base64.urlsafe_b64decode(derived)
        assert len(decoded) == 32

    def test_deterministic_for_same_input(self) -> None:
        """Same passphrase should always produce the same derived key."""
        key_a = _derive_key("deterministic-test")
        key_b = _derive_key("deterministic-test")
        assert key_a == key_b

    def test_different_passphrases_produce_different_keys(self) -> None:
        """Different passphrases should produce different derived keys."""
        key_a = _derive_key("passphrase-alpha")
        key_b = _derive_key("passphrase-beta")
        assert key_a != key_b

    def test_derived_key_is_valid_fernet_key(self) -> None:
        """The derived key should be usable to construct a Fernet instance."""
        derived = _derive_key("valid-fernet-test")
        fernet = Fernet(derived)
        # Should be able to encrypt/decrypt
        token = fernet.encrypt(b"hello")
        assert fernet.decrypt(token) == b"hello"


# ---------------------------------------------------------------------------
# get_fernet
# ---------------------------------------------------------------------------


class TestGetFernet:
    """Tests for the cached Fernet instance factory."""

    def test_returns_fernet_instance_with_passphrase(self) -> None:
        """get_fernet should return a Fernet instance when given a passphrase."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            fernet = get_fernet()
            assert isinstance(fernet, Fernet)

    def test_returns_fernet_instance_with_valid_key(self) -> None:
        """get_fernet should use the key directly when it is a valid Fernet key."""
        valid_key = _generate_valid_fernet_key()
        with patch("src.core.config.get_settings", return_value=_make_mock_settings(valid_key)):
            fernet = get_fernet()
            assert isinstance(fernet, Fernet)

    def test_caches_fernet_instance(self) -> None:
        """Repeated calls should return the same cached object."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            first = get_fernet()
            second = get_fernet()
            assert first is second

    def test_passphrase_derived_key_works_for_encryption(self) -> None:
        """A passphrase-derived Fernet should successfully encrypt and decrypt."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            fernet = get_fernet()
            token = fernet.encrypt(b"test-payload")
            assert fernet.decrypt(token) == b"test-payload"

    def test_direct_key_works_for_encryption(self) -> None:
        """A direct valid Fernet key should successfully encrypt and decrypt."""
        valid_key = _generate_valid_fernet_key()
        with patch("src.core.config.get_settings", return_value=_make_mock_settings(valid_key)):
            fernet = get_fernet()
            token = fernet.encrypt(b"test-payload")
            assert fernet.decrypt(token) == b"test-payload"


# ---------------------------------------------------------------------------
# encrypt_dict / decrypt_dict
# ---------------------------------------------------------------------------


class TestEncryptDecryptDict:
    """Tests for encrypt_dict and decrypt_dict round-trip."""

    def test_round_trip_simple_dict(self) -> None:
        """encrypt_dict -> decrypt_dict should return the original data."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data = {"api_key": "sk-live-abc123", "secret": "s3cret"}  # noqa: S105
            token = encrypt_dict(data)
            result = decrypt_dict(token)
            assert result == data

    def test_round_trip_nested_dict(self) -> None:
        """Nested dicts should survive the round trip."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data = {"outer": {"inner_key": "inner_value"}, "number": 42}
            token = encrypt_dict(data)
            result = decrypt_dict(token)
            assert result == data

    def test_round_trip_with_special_characters(self) -> None:
        """Unicode and special characters should be preserved."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data = {"name": "Tester", "emoji": "Hello World", "cyrillic": "Привет"}
            token = encrypt_dict(data)
            result = decrypt_dict(token)
            assert result == data

    def test_round_trip_empty_dict(self) -> None:
        """An empty dict should encrypt and decrypt correctly."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data: dict = {}
            token = encrypt_dict(data)
            result = decrypt_dict(token)
            assert result == data

    def test_encrypt_dict_returns_string(self) -> None:
        """encrypt_dict should return a URL-safe base64 string."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            token = encrypt_dict({"key": "value"})
            assert isinstance(token, str)
            # Should be decodable (Fernet tokens are base64)
            assert len(token) > 0

    def test_decrypt_with_wrong_key_raises_invalid_token(self) -> None:
        """Decrypting with a different key should raise InvalidToken."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings("key-alpha")):  # noqa: S106
            token = encrypt_dict({"secret": "data"})

        # Clear cache and switch to a different key
        get_fernet.cache_clear()

        with patch("src.core.config.get_settings", return_value=_make_mock_settings("key-beta")):  # noqa: S106
            with pytest.raises(InvalidToken):
                decrypt_dict(token)

    def test_decrypt_garbage_token_raises_invalid_token(self) -> None:
        """Decrypting a non-Fernet string should raise InvalidToken."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            with pytest.raises(InvalidToken):
                decrypt_dict("not-a-valid-fernet-token")

    def test_encrypt_dict_raises_type_error_for_non_serializable(self) -> None:
        """encrypt_dict should raise TypeError if data is not JSON-serializable."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            with pytest.raises(TypeError):
                encrypt_dict({"func": lambda x: x})  # type: ignore[dict-item]

    def test_encrypted_tokens_differ_for_same_data(self) -> None:
        """Two encryptions of the same data should produce different tokens (Fernet uses random IV)."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data = {"key": "value"}
            token_a = encrypt_dict(data)
            token_b = encrypt_dict(data)
            assert token_a != token_b

    def test_round_trip_with_list_values(self) -> None:
        """Dict with list values should survive round trip."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data = {"tags": ["a", "b", "c"], "nums": [1, 2, 3]}
            token = encrypt_dict(data)
            result = decrypt_dict(token)
            assert result == data

    def test_round_trip_with_boolean_and_null_values(self) -> None:
        """Dict with booleans and None should survive round trip."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            data = {"active": True, "deleted": False, "metadata": None}
            token = encrypt_dict(data)
            result = decrypt_dict(token)
            assert result == data


# ---------------------------------------------------------------------------
# mask_value
# ---------------------------------------------------------------------------


class TestMaskValue:
    """Tests for the mask_value string masking function."""

    def test_normal_string_masks_leading_characters(self) -> None:
        """A normal string should have leading characters replaced with *."""
        result = mask_value("sk-live-abc123xyz")
        assert result.endswith("3xyz")
        assert result.startswith("*")
        assert len(result) == len("sk-live-abc123xyz")

    def test_default_visible_is_four(self) -> None:
        """Default visible=4 should show the last 4 characters."""
        result = mask_value("abcdefgh")
        assert result == "****efgh"

    def test_custom_visible_count(self) -> None:
        """Custom visible parameter should control how many trailing chars are shown."""
        result = mask_value("abcdefgh", visible=2)
        assert result == "******gh"

    def test_short_string_returned_unchanged(self) -> None:
        """Strings shorter than or equal to visible should be returned as-is."""
        result = mask_value("abc", visible=4)
        assert result == "abc"

    def test_string_equal_to_visible_returned_unchanged(self) -> None:
        """String of exactly visible length should be returned as-is."""
        result = mask_value("abcd", visible=4)
        assert result == "abcd"

    def test_empty_string_returns_placeholder(self) -> None:
        """An empty string should return the '****' placeholder."""
        result = mask_value("")
        assert result == "****"

    def test_single_character_with_default_visible(self) -> None:
        """A single character with default visible=4 should be returned as-is."""
        result = mask_value("x")
        assert result == "x"

    def test_five_char_string_with_default_visible(self) -> None:
        """A 5-char string with visible=4 should mask only the first char."""
        result = mask_value("12345")
        assert result == "*2345"

    def test_visible_zero_prepends_stars(self) -> None:
        """visible=0 should prepend stars equal to string length (Python slice behavior)."""
        result = mask_value("secret", visible=0)
        # value[-0:] is the full string in Python, so result = "******" + "secret"
        assert result == "******secret"

    def test_visible_larger_than_string_returns_unchanged(self) -> None:
        """visible > len(string) should return the string unchanged."""
        result = mask_value("short", visible=10)
        assert result == "short"


# ---------------------------------------------------------------------------
# mask_dict
# ---------------------------------------------------------------------------


class TestMaskDict:
    """Tests for the recursive mask_dict function."""

    def test_masks_string_values(self) -> None:
        """String values should be masked."""
        data = {"api_key": "sk-live-abcdef1234"}
        result = mask_dict(data)
        assert result["api_key"].endswith("1234")
        assert result["api_key"].startswith("*")

    def test_replaces_non_string_values(self) -> None:
        """Non-string values should be replaced with '****'."""
        data = {"count": 42, "active": True, "items": [1, 2, 3]}
        result = mask_dict(data)
        assert result["count"] == "****"
        assert result["active"] == "****"
        assert result["items"] == "****"

    def test_recurses_into_nested_dicts(self) -> None:
        """Nested dicts should be masked recursively."""
        data = {"outer": {"inner_key": "inner_secret_value1234"}}
        result = mask_dict(data)
        assert isinstance(result["outer"], dict)
        assert result["outer"]["inner_key"].endswith("1234")

    def test_empty_dict_returns_empty_dict(self) -> None:
        """An empty dict should return an empty dict."""
        assert mask_dict({}) == {}

    def test_does_not_mutate_original(self) -> None:
        """mask_dict should return a new dict, not mutate the input."""
        data = {"key": "secret_value_1234"}
        result = mask_dict(data)
        assert data["key"] == "secret_value_1234"
        assert result["key"] != data["key"]

    def test_empty_string_value_masked_as_placeholder(self) -> None:
        """Empty string values should become '****' via mask_value."""
        data = {"empty": ""}
        result = mask_dict(data)
        assert result["empty"] == "****"

    def test_deeply_nested_dict(self) -> None:
        """Three levels of nesting should all be masked."""
        data = {"l1": {"l2": {"l3": "deep_secret_1234"}}}
        result = mask_dict(data)
        assert result["l1"]["l2"]["l3"].endswith("1234")
        assert result["l1"]["l2"]["l3"].startswith("*")

    def test_mixed_types(self) -> None:
        """A dict with mixed value types should be handled correctly."""
        data = {
            "string_val": "abcdefgh",
            "int_val": 99,
            "dict_val": {"nested": "inner1234"},
            "none_val": None,
        }
        result = mask_dict(data)
        assert result["string_val"] == "****efgh"
        assert result["int_val"] == "****"
        assert result["dict_val"]["nested"].endswith("1234")
        assert result["none_val"] == "****"


# ---------------------------------------------------------------------------
# is_encrypted
# ---------------------------------------------------------------------------


class TestIsEncrypted:
    """Tests for the is_encrypted check."""

    def test_encrypted_format_returns_true(self) -> None:
        """A dict with exactly one key '_encrypted' should be detected."""
        creds = {"_encrypted": "gAAAAABk..."}
        assert is_encrypted(creds) is True

    def test_plain_dict_returns_false(self) -> None:
        """A plain credentials dict should not be detected as encrypted."""
        creds = {"api_key": "sk-live-123", "secret": "abc"}  # noqa: S105
        assert is_encrypted(creds) is False

    def test_encrypted_with_extra_keys_returns_false(self) -> None:
        """A dict with '_encrypted' plus other keys should return False."""
        creds = {"_encrypted": "gAAAAABk...", "extra": "value"}
        assert is_encrypted(creds) is False

    def test_empty_dict_returns_false(self) -> None:
        """An empty dict should return False."""
        assert is_encrypted({}) is False

    def test_encrypted_with_empty_token_returns_true(self) -> None:
        """Even an empty token string counts as encrypted format."""
        creds = {"_encrypted": ""}
        assert is_encrypted(creds) is True


# ---------------------------------------------------------------------------
# encrypt_credentials / decrypt_credentials
# ---------------------------------------------------------------------------


class TestEncryptDecryptCredentials:
    """Tests for the higher-level credential encrypt/decrypt helpers."""

    def test_round_trip_credentials(self) -> None:
        """encrypt_credentials -> decrypt_credentials should return original."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            creds = {"client_id": "abc", "client_secret": "xyz"}
            encrypted = encrypt_credentials(creds)
            result = decrypt_credentials(encrypted)
            assert result == creds

    def test_encrypt_returns_encrypted_format(self) -> None:
        """encrypt_credentials should return {'_encrypted': '<token>'}."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            creds = {"key": "value"}
            encrypted = encrypt_credentials(creds)
            assert "_encrypted" in encrypted
            assert len(encrypted) == 1
            assert isinstance(encrypted["_encrypted"], str)

    def test_decrypt_unencrypted_returns_as_is(self) -> None:
        """decrypt_credentials with a plain dict should return it unchanged."""
        plain = {"api_key": "sk-123", "token": "tok-456"}  # noqa: S105
        result = decrypt_credentials(plain)
        assert result is plain

    def test_decrypt_with_wrong_key_raises_invalid_token(self) -> None:
        """Decrypting credentials with a different key should raise InvalidToken."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings("key-one")):  # noqa: S106
            encrypted = encrypt_credentials({"secret": "data"})

        get_fernet.cache_clear()

        with patch("src.core.config.get_settings", return_value=_make_mock_settings("key-two")):  # noqa: S106
            with pytest.raises(InvalidToken):
                decrypt_credentials(encrypted)

    def test_encrypted_dict_passes_is_encrypted_check(self) -> None:
        """The output of encrypt_credentials should pass is_encrypted."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            encrypted = encrypt_credentials({"key": "val"})
            assert is_encrypted(encrypted) is True

    def test_decrypt_empty_encrypted_dict(self) -> None:
        """Encrypting and decrypting an empty dict should work."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            encrypted = encrypt_credentials({})
            result = decrypt_credentials(encrypted)
            assert result == {}

    def test_decrypt_credentials_with_nested_data(self) -> None:
        """Nested credential dicts should survive round-trip."""
        with patch("src.core.config.get_settings", return_value=_make_mock_settings()):
            creds = {
                "oauth": {"access_token": "at-123", "refresh_token": "rt-456"},  # noqa: S105
                "api_key": "sk-789",
            }
            encrypted = encrypt_credentials(creds)
            result = decrypt_credentials(encrypted)
            assert result == creds
