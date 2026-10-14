import json
import subprocess

import pytest


SHELL_QUOTE = "/opt/code-server/lib/vscode/node_modules/shell-quote"
PROXY_ADDR = "/opt/code-server/node_modules/proxy-addr"


def run_node(source, *arguments):
    result = subprocess.run(
        ["node", "-e", source, *arguments],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("newline", ["\n", "\r", "\u2028", "\u2029"])
def test_shell_quote_rejects_line_terminators_after_comment(newline):
    run_node(r"""
const assert = require('node:assert/strict');
const {quote} = require(process.argv[1]);
const newline = JSON.parse(process.argv[2]);
assert.throws(() => quote(['echo', 'ok', {comment: 'x'}, `a${newline}id;#`]), TypeError);
""", SHELL_QUOTE, json.dumps(newline))


def test_shell_quote_preserves_literal_arguments():
    run_node(r"""
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {quote} = require(process.argv[1]);
const values = ['a b', "single'quote", '$(id);#', 'line\nbreak'];
const command = quote(['printf', '%s\\0', ...values]);
assert.equal(execFileSync('/bin/sh', ['-c', command], {encoding: 'utf8'}), values.join('\0') + '\0');
""", SHELL_QUOTE)


def test_proxy_addr_rejects_malformed_mapped_cidr_trust():
    run_node(r"""
const assert = require('node:assert/strict');
const proxyaddr = require(process.argv[1]);
const trust = proxyaddr.compile(['::ffff:10.0.0.0/8']);
assert.equal(trust('203.0.113.9'), false);
assert.equal(trust('::ffff:203.0.113.9'), false);
const req = {socket: {remoteAddress: '203.0.113.9'}, headers: {'x-forwarded-for': '198.51.100.42'}};
assert.equal(proxyaddr(req, trust), '203.0.113.9');
""", PROXY_ADDR)


def test_proxy_addr_preserves_trusted_proxy_resolution():
    run_node(r"""
const assert = require('node:assert/strict');
const proxyaddr = require(process.argv[1]);
for (const cidr of ['10.0.0.0/8', '::ffff:10.0.0.0/104']) {
  const trust = proxyaddr.compile([cidr]);
  assert.equal(trust('10.1.2.3'), true);
  assert.equal(trust('203.0.113.9'), false);
  const req = {socket: {remoteAddress: '10.1.2.3'}, headers: {'x-forwarded-for': '198.51.100.42'}};
  assert.equal(proxyaddr(req, trust), '198.51.100.42');
  assert.equal(proxyaddr(req, () => false), '10.1.2.3');
}
""", PROXY_ADDR)
