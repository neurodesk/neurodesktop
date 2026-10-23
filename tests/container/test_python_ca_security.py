from importlib.metadata import version
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.x509.oid import NameOID
from packaging.version import Version


def test_certifi_excludes_compromised_emsign_g3_roots():
    assert Version(version("certifi")) >= Version("2026.10.19")

    certificates = x509.load_pem_x509_certificates(
        Path(certifi.where()).read_bytes()
    )
    common_names = {
        attribute.value
        for certificate in certificates
        for attribute in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    }

    assert "emSign ECC Root CA - G3" not in common_names
    assert "emSign Root TLS CA - G3" not in common_names
