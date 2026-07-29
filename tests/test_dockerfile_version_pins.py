from pathlib import Path


def _dockerfile_text() -> str:
    checkout = Path(__file__).resolve().parents[1] / "Dockerfile"
    packaged = Path("/opt/tests/Dockerfile")
    target = checkout if checkout.exists() else packaged
    return target.read_text(encoding="utf-8")


def test_refreshed_runtime_and_builder_pins():
    dockerfile = _dockerfile_text()
    expected = (
        "ARG BASE_IMAGE_TAG=2026-07-28",
        "ARG APPTAINER_VERSION=1.5.3",
        "ARG APPTAINER_GO_VERSION=1.26.5",
        "ARG APPTAINER_GRPC_VERSION=1.82.1",
        'ARG CODE_SERVER_VERSION="4.130.0"',
        'ARG TOMCAT_VERSION="11.0.24"',
        'ARG OPENCODE_VERSION="1.18.7"',
        'ARG NODE_TAR_VERSION="7.5.22"',
    )
    for pin in expected:
        assert dockerfile.count(pin) == 1, pin


def test_cvmfs_client_and_repository_bootstrap_are_pinned():
    dockerfile = _dockerfile_text()

    assert "ARG CVMFS_VERSION=2.13.3+ubuntu24.04" in dockerfile
    assert "ARG CVMFS_RELEASE_VERSION=4.9" in dockerfile
    assert "ARG CVMFS_RELEASE_SHA256=" in dockerfile
    assert 'echo "${CVMFS_RELEASE_SHA256}  /tmp/cvmfs-release-latest_all.deb"' in dockerfile
    assert (
        "CVMFS release bootstrap changed; update CVMFS_RELEASE_VERSION and "
        "CVMFS_RELEASE_SHA256 together."
    ) in dockerfile
    assert 'dpkg-query -W -f=\'${Version}\' cvmfs-release' in dockerfile
    assert 'cvmfs="${CVMFS_VERSION}"' in dockerfile
