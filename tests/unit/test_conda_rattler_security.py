"""Build contracts for the temporary removal of vulnerable py-rattler."""

import re

from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def _conda_install_layer():
    matches = re.findall(
        r"^RUN .*?nb_conda_kernels.*?(?=^[A-Z]+ |\Z)",
        DOCKERFILE,
        re.MULTILINE | re.DOTALL,
    )
    assert len(matches) == 1
    return matches[0]


def test_vulnerable_rattler_is_removed_after_the_last_conda_install():
    layer = _conda_install_layer()
    install = "conda install --solver=libmamba"
    remove = "conda remove --yes --force-remove conda-rattler-solver py-rattler"

    assert len(re.findall(r"\bconda install\b", DOCKERFILE)) == 1
    assert install in layer
    assert remove in layer
    assert layer.index(install) < layer.index(remove)
    assert 'importlib.util.find_spec("rattler") is None' in layer


def test_conda_cannot_restore_the_vulnerable_py_rattler_line():
    layer = _conda_install_layer()

    assert "conda config --system --set solver libmamba" in layer
    assert (
        "conda config --system --append disallowed_packages "
        "'py-rattler <0.28.0'"
    ) in layer
    assert "test \"$(conda config --show solver)\" = 'solver: libmamba'" in layer
    assert "conda info --json >/dev/null" in layer


def test_conda_keeps_libmamba_off_the_broken_local_channel_release():
    layer = _conda_install_layer()
    install = "'libmamba=2.9.0' 'libmambapy=2.9.0' 'mamba=2.9.0'"

    assert install in layer
    assert layer.index("nb_conda_kernels") < layer.index(install)
    for package in ("libmamba", "libmambapy"):
        assert (
            "conda config --system --append disallowed_packages "
            f"'{package} ==2.10.0'"
        ) in layer


def test_cve_is_not_suppressed_from_the_image_scan():
    policy = repo_path(".trivyignore.yaml").read_text(encoding="utf-8")

    assert "CVE-2026-115905" not in policy
