from testlib import repo_path


def test_glass_install_preserves_directory_traversal_and_private_file_ownership():
    dockerfile = repo_path("Dockerfile").read_text()
    assert "COPY --chmod=0755 config/glass/ /opt/neurodesktop/glass/" in dockerfile
    assert "find /opt/neurodesktop/glass -type f -exec chmod 0644 {} +" in dockerfile

