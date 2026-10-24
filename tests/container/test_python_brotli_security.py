"""Exercise the fixed Brotli decoder installed in the runtime image."""

from importlib.metadata import version

import brotli
from packaging.version import Version


MINIMUM_FIXED_VERSION = Version("1.2.1")


def test_python_brotli_decoder_is_fixed_and_functional():
    assert Version(version("Brotli")) >= MINIMUM_FIXED_VERSION

    payload = b"Neurodesktop Brotli regression check\n" * 64
    assert brotli.decompress(brotli.compress(payload)) == payload
