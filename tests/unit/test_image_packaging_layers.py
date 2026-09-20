"""Deleted payloads must never be committed to a runtime image layer."""

import re

from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")
INSTRUCTIONS = re.findall(
    r"^[A-Z]+ .*?(?=^[A-Z]+ |\Z)", DOCKERFILE, re.MULTILINE | re.DOTALL
)


def instruction_containing(text):
    """Locate one Dockerfile layer without silently accepting duplicate matches."""
    matches = [instruction for instruction in INSTRUCTIONS if text in instruction]
    assert len(matches) == 1, text
    return matches[0]


def test_guacamole_archive_is_extracted_and_removed_in_its_download_layer():
    """Prevent the WAR archive from surviving in a lower image layer."""
    layer = instruction_containing("-o /tmp/guacamole-${GUACAMOLE_VERSION}.war")
    assert "&& unzip -q /usr/local/tomcat/webapps/ROOT.war" in layer
    assert "&& rm /usr/local/tomcat/webapps/ROOT.war" in layer
    assert "<max-age>86400</max-age>" in layer
    assert "chmod -R a+rX /usr/local/tomcat/webapps/ROOT" in layer


def test_development_headers_are_installed_and_purged_in_one_layer():
    """Keep compilation headers out of every committed runtime layer."""
    for package in ("libgpgme-dev", "libossp-uuid-dev"):
        install = instruction_containing(
            "apt-install-retry build-essential libgpgme-dev libossp-uuid-dev"
        )
        purge = re.search(r"apt-get purge[^\n]+", install).group()
        assert package in purge
        assert not any(
            package in instruction
            for instruction in INSTRUCTIONS
            if instruction != install
        )


def test_apptainer_cni_is_stripped_before_runtime_copy():
    """Exclude unused CNI binaries before copying Apptainer into the runtime stage."""
    cleanup = instruction_containing("rm -rf /opt/apptainer/libexec/apptainer/cni")
    assert "./scripts/install-dependencies" in cleanup
    assert DOCKERFILE.index(cleanup) < DOCKERFILE.index("AS builder")


def test_wheel_frontends_are_removed_in_the_pip_install_layer():
    """Remove superseded wheel bundles before their replacement source builds."""
    layer = instruction_containing("apt-install-retry build-essential libgpgme-dev")
    assert "pip install --build-constraint" in layer
    assert 'rm -rf "${SITE_PACKAGES}/${package}/labextension"' in layer
    for package in (
        "notebook_intelligence", "jupyterlab_myst",
        "jupyter_collaboration_ui", "jupyter_docprovider",
    ):
        assert package in layer.split("for package in ", 1)[1].split(";", 1)[0]
    for extension in (
        "@plmbr/notebook-intelligence", "jupyterlab-myst",
        "@jupyter/collaboration-extension", "@jupyter/docprovider-extension",
    ):
        assert f"/opt/conda/share/jupyter/labextensions/{extension}" in layer


def test_myst_has_one_rebuilt_copy():
    """Expose the rebuilt MyST bundle through a symlink instead of a second copy."""
    layer = instruction_containing("cp -a /tmp/myst/jupyterlab_myst/labextension")
    assert 'ln -s "${MYST_LABEXT_DIR}" "${APP_MYST_DIR}"' in layer
    assert 'cp -a "${MYST_LABEXT_DIR}" "${APP_MYST_DIR}"' not in layer
