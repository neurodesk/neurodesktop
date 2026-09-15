"""Build contract for the opt-in, static Tailscale binaries."""

import re

from testlib import repo_path


def test_tailscale_install_verifies_both_architectures_and_removes_archive():
    dockerfile = repo_path("Dockerfile").read_text()
    block = dockerfile.split('# Install only the static Tailscale CLI', 1)[1].split(
        '# Install the headless T3 Code server', 1
    )[0]
    assert re.search(r'ARG TAILSCALE_VERSION="\d+\.\d+\.\d+"', block)
    for arch in ("amd64", "arm64"):
        assert re.search(rf'ARG TAILSCALE_{arch.upper()}_SHA256="[0-9a-f]{{64}}"', block)
        assert f'{arch}) tailscale_sha256="${{TAILSCALE_{arch.upper()}_SHA256}}"' in block
    assert 'dpkg --print-architecture' in block
    assert 'Unsupported Tailscale architecture:' in block
    assert 'https://pkgs.tailscale.com/stable/${tailscale_archive}.tgz' in block
    assert block.index('sha256sum -c -') < block.index('tar -xzf')
    assert '"${tailscale_archive}/tailscale" "${tailscale_archive}/tailscaled"' in block
    assert 'install -m 0755' in block
    assert '/usr/local/bin/' in block
    assert 'rm -rf /tmp/tailscale.tgz "/tmp/${tailscale_archive}"' in block
    assert 'systemctl' not in block
    assert 'tailscale up' not in block
