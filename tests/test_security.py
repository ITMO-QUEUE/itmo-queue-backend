import hashlib

import bcrypt

from itmo_queue.core.security import get_password_hash, verify_password


def test_long_unicode_password_is_not_truncated():
    password = "пароль" * 30
    hashed = get_password_hash(password)
    assert hashed.startswith("bcrypt-sha256:")
    assert verify_password(password, hashed)
    assert not verify_password(password[:-1] + "x", hashed)
    assert not verify_password(password[:72], hashed)


def test_previous_hash_formats_and_invalid_hash():
    password = "previous-password"
    raw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    legacy_sha = bcrypt.hashpw(
        hashlib.sha256(password.encode()).hexdigest().encode(), bcrypt.gensalt()
    ).decode()
    assert verify_password(password, raw)
    assert verify_password(password, legacy_sha)
    assert not verify_password("wrong", raw)
    assert not verify_password("wrong", legacy_sha)
    assert not verify_password("x" * 100, raw)
    assert not verify_password(password, "invalid database value")
