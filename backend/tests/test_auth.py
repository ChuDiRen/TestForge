"""认证原语单测：口令哈希、token 签发/校验/过期/防篡改。"""

import time
from unittest import mock

from services.shared.auth import hash_password, issue_token, parse_token, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("s3cret-密码")
    assert "$" in stored and stored != "s3cret-密码"
    assert verify_password("s3cret-密码", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret-密码", "garbage")


def test_token_roundtrip():
    token = issue_token("admin", "admin", ttl_s=60)
    info = parse_token(token)
    assert info is not None
    assert info["username"] == "admin" and info["role"] == "admin" and "exp" in info


def test_token_expired():
    token = issue_token("admin", "admin", ttl_s=-5)
    assert parse_token(token) is None


def test_token_tamper_proof():
    token = issue_token("admin", "admin", ttl_s=60)
    assert parse_token(token + "x") is None
    # 改角色但保留签名 → 签名不匹配
    parts = token.split(":")
    forged = f"{parts[0]}:{parts[1]}:admin:{parts[3]}" if parts[2] != "admin" else f"{parts[0]}:{parts[1]}:viewer:{parts[3]}"
    assert parse_token(forged) is None
    assert parse_token("not-a-token") is None
    assert parse_token("") is None


def test_token_rejects_unknown_role():
    with mock.patch("services.shared.auth._sign", return_value="sig"):
        assert parse_token(f"u:{int(time.time()) + 60}:superuser:sig") is None


def test_regression_short_code_mapping():
    """runner junit 名回映射口径：全码 → 短码；跨文件分组回归依赖此对齐。"""
    from gateway.regression import short_code

    assert short_code("CASE-ABC123-TC-007") == "TC-007"
    assert short_code("CASE-XYZ999-TC-012") == "TC-012"
    assert short_code("TC-003") == "TC-003"
    assert short_code("CASE-NOTC-001") == "CASE-NOTC-001"
