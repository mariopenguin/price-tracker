from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from app.auth import hash_password, verify_password, create_access_token, decode_access_token

def test_password_hash_and_verify():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed)
    assert not verify_password("wrong", hashed)

def test_token_roundtrip():
    token = create_access_token({"sub": "42"})
    payload = decode_access_token(token)
    assert payload["sub"] == "42"

def test_invalid_token_returns_none():
    result = decode_access_token("not.a.token")
    assert result is None

def test_wrong_key_token_returns_none():
    # A token signed with a different key should fail
    bad_token = pyjwt.encode({"sub": "99"}, "wrong-key", algorithm="HS256")
    result = decode_access_token(bad_token)
    assert result is None

def test_expired_token_returns_none():
    expired_token = pyjwt.encode(
        {"sub": "99", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        "test-secret-key-for-development-only",
        algorithm="HS256",
    )
    assert decode_access_token(expired_token) is None
