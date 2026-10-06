import re

from packaging.version import Version
import pytest

from testlib import repo_path


def builder_source():
    dockerfile = repo_path("Dockerfile").read_text()
    return dockerfile.split(" AS builder", maxsplit=1)[1].split("\nFROM ", maxsplit=1)[0]


@pytest.mark.parametrize("argument,minimum,package_path", [
    ("SHELL_QUOTE_VERSION", "1.11.0", "/opt/code-server/lib/vscode/node_modules/shell-quote"),
    ("PROXY_ADDR_VERSION", "2.0.8", "/opt/code-server/node_modules/proxy-addr"),
])
def test_builder_pins_and_checks_dependency_before_runtime_copy(argument, minimum, package_path):
    dockerfile = repo_path("Dockerfile").read_text()
    builder = builder_source()
    pin = re.search(rf'^ARG {argument}="([^"\n]+)"$', builder, re.MULTILINE)
    assert pin is not None
    assert Version(pin.group(1)) >= Version(minimum)
    assert f'require("{package_path}/package.json").version\')" = "${{{argument}}}"' in builder
    assert "cp -a /tmp/code-server /opt/code-server" in dockerfile


def test_nested_shell_quote_is_replaced_and_archive_removed():
    dockerfile = builder_source()
    assert 'npm pack --silent "shell-quote@${SHELL_QUOTE_VERSION}"' in dockerfile
    assert 'rm -rf "${shell_quote_dir}"' in dockerfile
    assert 'tar -xzf "${shell_quote_tar}" -C "${shell_quote_dir}" --strip-components=1' in dockerfile
    assert 'rm -f "${shell_quote_tar}"' in dockerfile
    assert 'npm cache clean --force' in dockerfile


def test_proxy_addr_install_updates_npm_metadata():
    dockerfile = builder_source()
    assert 'npm install --save-exact --ignore-scripts --no-audit --no-fund "proxy-addr@${PROXY_ADDR_VERSION}"' in dockerfile
