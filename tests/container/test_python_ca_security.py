from importlib import import_module
from importlib.metadata import version
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from packaging.version import Version


@pytest.mark.parametrize(
    ("package_name", "module_name", "minimum_version"),
    [
        ("certifi", "certifi", "2026.10.19"),
        ("pip", "pip._vendor.certifi", "26.2.2"),
    ],
)
def test_python_ca_bundles_exclude_compromised_emsign_g3_roots(
    package_name, module_name, minimum_version
):
    assert Version(version(package_name)) >= Version(minimum_version)
    bundle = import_module(module_name)
    assert Version(bundle.__version__) >= Version("2026.10.19")

    certificates = x509.load_pem_x509_certificates(
        Path(bundle.where()).read_bytes()
    )
    common_names = {
        attribute.value
        for certificate in certificates
        for attribute in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    }

    assert "emSign ECC Root CA - G3" not in common_names
    assert "emSign Root TLS CA - G3" not in common_names
