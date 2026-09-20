---
title: Image security audit
description: Source audit of image credentials, service isolation, privileges, and release controls
parent: index.md
status: assessed
last-reviewed: "2026-09-20"
---

# Image security audit

This assessment covers commit `e3c31ec0fe7219bb73cd5873fff433e982b867f5`.
It reviews the Dockerfile, desktop startup, Jupyter proxy configuration,
T3 authentication code, credential persistence, and production publication workflow.
See [Architecture](../architecture.md), [runtime variables](../environment-variables.md),
and [testing](../testing.md) for the current operational contracts.

Follow-up changes address findings 1, 2, 4, and the default sudo policy in 5.
See [desktop security](../architecture/desktop.md#credentials-and-service-access)
and [startup privileges](../environment-variables.md#startup-privileges) for the
implemented behavior. The findings below retain the original audit snapshot.

The most urgent improvements are removing the fixed OS password and isolating
unauthenticated services on shared HPC nodes. Credential persistence and release
gating also need changes. No runtime settings were changed by this audit.

## Scope and evidence limits

Findings below distinguish source evidence, local reproduction, and deployment
conditions. Docker image enumeration succeeded, but no matching full Neurodesktop
image was installed. The local agentic worker image is a different artifact.
No full image build, vulnerability-database scan, live listener inventory, or
cross-user exploit was performed. This report does not certify a published image
digest or claim that it has no known CVEs.

A trusted user's Jupyter session already permits arbitrary code execution.
The important boundaries are unauthenticated clients, other host users, container
privileges, mounted host data, and credentials available to agent subprocesses.
An unpublished container port is not automatically reachable from the internet.
Container peers, host networking, and Apptainer's shared network namespace create
different exposure conditions.

## Findings

### 1. High: fixed OS password remains usable by RDP

The [Dockerfile](../../Dockerfile) sets the notebook user's password to `password`
at line 1176. [before_notebook.sh](../../config/jupyter/before_notebook.sh)
resets it to the same value on every root startup at line 286. Changing the
password interactively therefore does not survive that startup path.
The [RDP mapping](../../config/guacamole/user-mapping-vnc-rdp.xml) embeds the same
credential. Rotation of the Guacamole web and VNC secrets does not rotate this
OS credential.

[ensure_rdp_backend.sh](../../config/guacamole/ensure_rdp_backend.sh) starts xrdp
without restricting its listening address. A numeric xrdp port listens on all
interfaces according to the [upstream manual](https://github.com/neutrinolabs/xrdp/blob/devel/docs/man/xrdp.ini.5.in).
When the RDP service is reachable, the fixed password provides a route to the
desktop without Jupyter authentication. Live reachability remains unverified.
The unprivileged Apptainer launcher already hides RDP, which limits this finding
for that path.

Remove the startup reset. Use a private, generated OS credential for RDP and
update its mapping together, or disable password login and RDP in a hardened
profile. Bind xrdp to loopback when it is only a Guacamole backend. Test an
actual RDP login and restart, including rejection of the old fixed credential.

### 2. High on shared HPC: VS Code has no backend authentication

The [Jupyter proxy template](../../config/jupyter/jupyter_notebook_config.py.template)
starts code-server with `--auth none --bind-addr 127.0.0.1:{port}` at lines 98–104.
Other users in the same host network namespace can reach that port without
passing through Jupyter. A different port per user prevents collisions but does
not enforce access control. Code-server exposes the notebook user's files and
terminal execution.

Use a Unix socket inside a user-only directory, or backend authentication with
privately stored credentials. Jupyter Server Proxy explicitly supports
[private Unix sockets](https://jupyter-server-proxy.readthedocs.io/en/latest/server-process.html).
Add a two-UID test that proves the owner can connect through Jupyter and the
second UID cannot connect directly. Also review loopback guacd access under this
same threat model. T3 has separate session authentication and should not be
classified as another unauthenticated backend.

### 3. High with readable home directories: credential writes weaken permissions

[nbi_setup.sh](../../config/agents/nbi_setup.sh) creates a predictable temporary
configuration file with normal `open(..., "w")` and replaces the original at
lines 435–440. It does not preserve private permissions or explicitly set mode
0600. Executing that exact embedded Python block under umask 022 changed a
0600 configuration to 0644 while storing a dummy API key.

[opencode](../../config/agents/opencode) also persists API and MCP credentials in
`.bashrc`. Its copy operation preserves an existing destination's permissions,
which can leave credentials in a readable shell startup file. Disclosure to
another UID requires directory traversal permissions; a private home directory
mitigates it. The file-mode regression is still present.

Keep credentials in a dedicated mode-0600 file under a mode-0700 directory.
Use exclusively created temporary files in that directory and atomic replacement,
with explicit private permissions. Migrate existing secrets out of `.bashrc`.
Test first writes and replacements under umasks 022 and 077.

### 4. Medium: API credentials can be injected into a lookalike endpoint

At line 425, [nbi_setup.sh](../../config/agents/nbi_setup.sh) checks
`"llm.neurodesk.org" in base_url` before injecting the Neurodesk key.
The local reproduction accepted `https://llm.neurodesk.org.attacker.invalid/openai`
and wrote the dummy credential into its model configuration. A subsequent model
request could send the credential there. This requires influence over the model
configuration; it is not an unauthenticated remote exploit by itself.

Parse the URL and require HTTPS and an exact normalized hostname, with an
explicit port policy. Apply the same rule to every provider-selection check.
Test lookalike hostnames, user-info, query strings, and HTTP URLs. No real key or
outbound model request was used in this audit.

### 5. High impact when combined with compromise: unrestricted sudo is the default

[start_notebook.sh](../../config/jupyter/start_notebook.sh) defaults `GRANT_SUDO`
to `yes` and installs `NOPASSWD:ALL` at lines 31–35. The image ends with
`USER root`, though Jupyter startup can subsequently drop privileges. These are
distinct facts; this report does not assert that Jupyter always runs as root.

The shipped [agent configuration](../../config/agents/codex_config.toml) also
explicitly selects no approvals and full access. These choices increase the
consequences of a compromised session, dependency, or agent command.
The production image tests use `--privileged`; that is evidence of the tested
deployment profile, not proof that every deployment uses it.

Offer a tested profile with `GRANT_SUDO=no`, an unprivileged steady-state user,
minimal mounts and capabilities, and sandboxed agent execution. Keep required
administrative setup in a separate startup phase. Test CVMFS, FUSE, Apptainer,
Slurm, and desktop behavior before changing the default. Avoid prescribing
`no-new-privileges` as an untested drop-in when existing startup needs sudo.
Docker documents that [privileged mode grants all capabilities and host-device access](https://docs.docker.com/engine/containers/run/).

### 6. Medium: internal Tomcat and SSH services lack consistent listener restrictions

The Dockerfile and [guacamole.sh](../../config/guacamole/guacamole.sh) change the
Tomcat connector port but do not set its address. Tomcat's documented default
is [all local addresses](https://tomcat.apache.org/tomcat-11.0-doc/config/http.html).
Guacamole still requires its generated web credential; this is unnecessary
exposure, not a demonstrated authentication bypass.

The primary [SSH configuration](../../config/ssh/sshd_config) has no active
`ListenAddress`. Only the fallback configuration in
[ensure_sftp_sshd.sh](../../config/ssh/ensure_sftp_sshd.sh) binds loopback.
The startup command forces `StrictModes=no` in both paths. Password and keyboard
interactive authentication are disabled, which is good, but ownership checks
are removed even when they could succeed. The normal configuration also permits
SSH functionality beyond an SFTP-only side channel.

Bind these internal services explicitly to loopback and validate IPv4 and IPv6.
Preserve SSH ownership checks where possible. If only SFTP is required, restrict
the account, command, and forwarding options after verifying the Guacamole
integration. OpenSSH documents these controls in
[sshd_config](https://man.openbsd.org/sshd_config).

### 7. Medium: security scan failures do not block release publication

In [build-neurodesktop.yml](../../.github/workflows/build-neurodesktop.yml),
`merge-manifests`, `test-image`, and `scan-image` each depend only on `build-image`.
Production manifests and external registry copies can publish before tests and
scans finish. A later failed scan makes the workflow red without retracting the
published release.

The scanner fails only on CRITICAL findings. The
[ignore file](../../.trivyignore.yaml) contains reasoned exceptions but does not
scope them to individual package paths or expiry dates. In particular, the
SSH-library exception is justified for specific binaries but applies by CVE ID.

Build immutable candidate digests, test and scan both architectures, then promote
those same digests after both jobs pass. Consider gating fixable HIGH findings,
with reviewed exceptions, while retaining a complete report. Scope exceptions
to the affected artifacts, assign review dates, and retain an SBOM and scan
results per release. Re-scan released digests as vulnerability databases change.

### 8. Medium: executable downloads are not uniformly verified

The Dockerfile uses remote shell installers for NodeSource, Nextflow, Claude,
and OpenCode. Several downloaded archives lack a repository-owned checksum,
and some source clones follow a moving branch. HTTPS and a version argument
do not make a changing installer or archive reproducible.

Extend the existing checksum approach used for CVMFS, Tailscale, and cloudflared
to remaining executable downloads. Pin source commits and base-image digests.
Keep deliberate dependency updates and compatibility probes so immutable pins
do not become neglected security updates. Produce build provenance and verify
release signatures where upstream provides them.

## Existing protections worth preserving

- Guacamole startup refuses to serve when secret initialization fails. Its web
  and VNC credentials are generated per user, with private secret files.
- VNC and guacd explicitly bind loopback. This blocks off-host access, although
  loopback alone does not isolate users sharing a network namespace.
- T3's Jupyter handlers require authentication, use a short-lived pairing
  credential, and keep upstream credentials out of ordinary error responses.
- The Apptainer configuration disables setuid operation. This does not by itself
  establish whether every installed setuid file has been removed.
- Both image architectures already receive a Trivy scan, and the build includes
  targeted dependency remediation and some checksum-verified downloads.

## Verification performed

The following existing tests were run:

```sh
pytest -q tests/unit/test_t3_code_web.py tests/unit/test_t3_connect.py \
  tests/unit/test_desktop_launcher_modes.py tests/unit/test_node_tar_security.py \
  tests/unit/test_apptainer_crypto_security.py
```

Result: 41 passed, one module skipped, one test failed. The T3 web module skipped
because `jupyter_server_proxy` is unavailable. The cancellation test failed
because host Python 3.10 lacks `asyncio.timeout`; the extension declares Python
3.11 or later and CI uses 3.12. This is a local validation limitation, not evidence
of that failure in the image. Passing source tests are not proof of live service
isolation.

This self-contained reproduction executes the actual key-injection block using
temporary files and a dummy key. Run from the repository root:

```python
from pathlib import Path
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

source = Path("config/agents/nbi_setup.sh").read_text()
blocks = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)
block = next(b for b in blocks if 'api_key = os.environ["NBI_API_KEY"]' in b)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "config.json"
    path.write_text(json.dumps({"chat_model": {
        "provider": "openai-compatible",
        "properties": [
            {"id": "base_url", "value": "https://llm.neurodesk.org.attacker.invalid/openai"},
            {"id": "api_key", "value": ""},
        ],
    }}))
    path.chmod(0o600)
    environment = dict(os.environ, NBI_CONFIG_FILE=str(path), NBI_API_KEY="AUDIT-DUMMY-KEY")
    previous_umask = os.umask(0o022)
    try:
        subprocess.run([sys.executable, "-c", block], env=environment, check=True)
    finally:
        os.umask(previous_umask)
    config = json.loads(path.read_text())
    print(config["chat_model"]["properties"][1]["value"] == "AUDIT-DUMMY-KEY")
    print(oct(stat.S_IMODE(path.stat().st_mode)))
```

Observed output was `True` and `0o644`.

## Follow-up acceptance checks

Use a built image identified by digest for the remaining checks:

1. Inventory listeners after opening each launcher. Confirm internal services
   reject remote connections and require the expected authentication.
2. Test two independent UIDs sharing an HPC network namespace. Verify the second
   UID cannot use the first user's VS Code, desktop credentials, or private files.
3. Verify generated credentials survive restart and fixed legacy passwords fail.
4. Scan both architectures, retain complete package inventories and findings,
   and reconcile every scanner exception with the installed artifact.
5. Exercise the reduced-privilege profile with desktop, CVMFS, Apptainer, and
   Slurm. Inventory effective capabilities, mounts, and setuid executables.
6. Verify a failing image test or security scan prevents production tag promotion.

Return to [Design records](index.md).
