"""Exercise PyJWT's asymmetric-key rejection in the installed image."""

import base64
import hashlib
import hmac

import jwt
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jwt.exceptions import InvalidKeyError


PUBLIC_KEY = b"""\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtN4O/YyL0/JqcjJE2C4V
OEjrkF76mZhc2HlJyqemmoS69Xd4buYOG1hfXEgTJzF3zVscQ+bWLZYqz3mzU7Ku
qmg6SbIYWTK0QeYzljK9MFkhkjdxV8zYYdwqhmCRwWl0t6R28U+pY/rF+Iso15Y8
isNCVbXvxNKR8unWVhkjPAGB58XhUUDRQqmtSOVuspd/K8+NDQ4LrUCJh1+zUZFd
LjSq5Xu48JYF+TvVy5Atns/uqq/Kd+gwUnMevsxhEJK292x1WPosH2PUvYx99Uht
mPozqUHZf0g3UdjbrR/m/l72+FtCad2J2/UOQ7u721SHXthq0xd55eU53uwb3SP8
JwIDAQAB
-----END PUBLIC KEY-----
"""


@pytest.mark.parametrize("public_key", [
    PUBLIC_KEY.replace(b"-----END", b"\t-----END"),
    PUBLIC_KEY.replace(b"\n", b"\r"),
    PUBLIC_KEY.replace(b"\n", b" ").strip(),
], ids=["indented-end-marker", "cr-only", "single-line"])
def test_noncanonical_public_key_cannot_be_used_as_an_hmac_secret(public_key):
    """Reject a PEM form the cryptography loader accepts (CVE-2026-102268)."""
    load_pem_public_key(public_key)

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(b'{"sub":"forged-admin"}').rstrip(b"=")
    signing_input = header + b"." + payload
    signature = hmac.new(public_key, signing_input, hashlib.sha256).digest()
    token = signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")

    with pytest.raises(InvalidKeyError):
        jwt.decode(token, public_key, algorithms=["RS256", "HS256"])

    with pytest.raises(InvalidKeyError):
        jwt.encode({"sub": "forged-admin"}, public_key, algorithm="HS256")
