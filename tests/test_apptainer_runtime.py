from pathlib import Path


def _read_dockerfile():
    test_file = Path(__file__).resolve()
    candidates = (
        test_file.parents[1] / "Dockerfile",
        test_file.parent / "Dockerfile",
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    candidate_list = ", ".join(str(path) for path in candidates)
    raise AssertionError(f"Could not find Dockerfile in: {candidate_list}")


def _read_environment_variables():
    test_file = Path(__file__).resolve()
    candidates = (
        test_file.parents[1] / "config/jupyter/environment_variables.sh",
        Path("/opt/neurodesktop/environment_variables.sh"),
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    candidate_list = ", ".join(str(path) for path in candidates)
    raise AssertionError(
        f"Could not find environment_variables.sh in: {candidate_list}"
    )


def test_apptainer_is_source_built_with_scanner_fixed_go_dependencies():
    dockerfile = _read_dockerfile()

    assert "ARG APPTAINER_VERSION=1.5.0-rc.2" in dockerfile
    assert "ARG APPTAINER_GO_VERSION=1.25.7" in dockerfile
    assert "ARG APPTAINER_GRPC_VERSION=1.79.3" in dockerfile
    assert (
        "FROM golang:${APPTAINER_GO_VERSION}-bookworm AS apptainer" in dockerfile
    )
    assert "FROM ghcr.io/apptainer/apptainer:" not in dockerfile
    assert (
        'git clone --depth 1 --branch "v${APPTAINER_VERSION}" '
        "https://github.com/apptainer/apptainer.git /tmp/apptainer"
    ) in dockerfile
    assert (
        'go get "google.golang.org/grpc@v${APPTAINER_GRPC_VERSION}"'
        in dockerfile
    )
    assert "go mod tidy" in dockerfile
    assert "go mod download" in dockerfile
    assert 'printf \'%s\\n\' "${APPTAINER_VERSION}" > VERSION' in dockerfile
    assert "./mconfig --prefix=/opt/apptainer --with-suid" in dockerfile
    assert "make -C builddir install" in dockerfile
    assert "./scripts/install-dependencies" in dockerfile


def test_non_root_apptainer_runtime_uses_writable_tmpfs():
    environment = _read_environment_variables()

    assert 'is_apptainer_runtime && [ "${EUID}" -ne 0 ]' in environment
    assert 'neurodesk_singularity_opts=" --writable-tmpfs "' in environment
    assert 'neurodesk_singularity_opts=" --overlay /tmp/apptainer_overlay "' in environment
