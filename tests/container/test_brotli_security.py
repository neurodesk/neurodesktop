"""Check that the installed image replaced the vulnerable Brotli decoder."""

import importlib.metadata

import brotli
from packaging.version import Version


def test_main_python_environment_has_one_fixed_brotli_release():
    releases = [
        dist.version for dist in importlib.metadata.distributions()
        if dist.metadata["Name"].lower() == "brotli"
    ]

    assert len(releases) == 1, releases
    assert Version(releases[0]) >= Version("1.2.1")


def test_fixed_decoder_round_trips_data():
    payload = b"neurodesk" * 4096
    assert brotli.decompress(brotli.compress(payload)) == payload
