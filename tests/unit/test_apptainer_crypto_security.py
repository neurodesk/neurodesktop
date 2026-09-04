"""Guard scanner-fixed Go crypto modules in the Apptainer build."""

import re

from testlib import repo_path


MINIMUM_CRYPTO_VERSION = (0, 55, 0)


def test_apptainer_crypto_override_is_fixed_and_applied_to_both_builds():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    match = re.search(
        r"^ARG APPTAINER_CRYPTO_VERSION=(\d+)\.(\d+)\.(\d+)$",
        dockerfile,
        re.MULTILINE,
    )

    assert match is not None
    assert tuple(map(int, match.groups())) >= MINIMUM_CRYPTO_VERSION

    apptainer_stage = dockerfile.split(
        "FROM golang:${APPTAINER_GO_VERSION}-bookworm AS apptainer",
        maxsplit=1,
    )[1].split("FROM quay.io/jupyter/base-notebook", maxsplit=1)[0]

    crypto_dependency = '"golang.org/x/crypto@v${APPTAINER_CRYPTO_VERSION}"'
    crypto_check = (
        'go list -m -f \'{{.Version}}\' golang.org/x/crypto)" '
        '= "v${APPTAINER_CRYPTO_VERSION}"'
    )
    assert apptainer_stage.count(crypto_dependency) == 2
    assert apptainer_stage.count(crypto_check) == 2
    assert (
        'gocryptfs_archive="$(echo gocryptfs_v*_src-deps.tar.gz)"'
        in apptainer_stage
    )
    assert "go mod vendor" in apptainer_stage
