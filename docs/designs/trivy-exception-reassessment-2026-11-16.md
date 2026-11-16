---
title: Trivy exception reassessment, 16 November 2026
description: Why all twenty image-scan exceptions were removed instead of
  extended past their 2026-11-20 expiry
parent: index.md
status: implemented
last-reviewed: "2026-11-16"
---

# Trivy exception reassessment, 16 November 2026

The image-scan policy held twenty exceptions. Thirteen expired on 2026-11-20;
seven had no expiry or package scope. All twenty were removed because none is
reported any longer.

## Evidence

The release published on 2026-11-14
(`ghcr.io/neurodesk/neurodesktop@sha256:68fd7ff6af8cc63e5eae8a748304c178ff3a2aebc3917906fb58a11ad40d1ad7`)
was scanned on 2026-11-16 with Trivy 0.74.0 and an empty ignore file, once per
architecture.

| Scan | amd64 | arm64 |
| --- | --- | --- |
| CRITICAL findings, the release gate | 0 | 0 |
| Excepted CVEs reported at any severity | 0 of 20 | 0 of 20 |
| Installed `linux-libc-dev` | 7.0.0-43.43 | 7.0.0-43.43 |

## Kernel header exceptions

Nineteen exceptions covered kernel CVEs that Trivy reported against
`linux-libc-dev`, the UAPI header package CVMFS pulls in through `uuid-dev`.
The [Ubuntu CVE tracker](https://ubuntu.com/security/cves) now lists every one
as released for Ubuntu 26.04 at or before the installed version.

| Fixed in `linux` | CVEs |
| --- | --- |
| 7.0.0-28.28 to 7.0.0-38.38 | CVE-2026-53215, CVE-2026-53398, CVE-2026-63940, CVE-2026-64535, CVE-2026-64564, CVE-2026-74394 |
| 7.0.0-42.42 | CVE-2026-89530, CVE-2026-89652, CVE-2026-89659, CVE-2026-89708, CVE-2026-90037, CVE-2026-93221, CVE-2026-98365, CVE-2026-98571, CVE-2026-98621 |
| 7.0.0-43.43 | CVE-2026-90162, CVE-2026-98432, CVE-2026-98725, CVE-2026-98805 |

The first six had no package scope or expiry, so they would have hidden the
same CVE in any package indefinitely.

## Go SSH exception

CVE-2026-56854 was excepted for `golang.org/x/crypto/ssh` in the Apptainer and
gocryptfs binaries because neither runs an SSH server. The image now builds
both against the pinned `APPTAINER_CRYPTO_VERSION`, and the finding is no
longer reported.

## Remaining exposure

`linux-libc-dev` stays installed, so a future kernel CVE rated CRITICAL can
fail the scan until Ubuntu publishes headers built from the fixed source. The
[build reference](../architecture/build.md#publication) describes how to handle
that. The policy test now requires package scope and an expiry within 90 days
for any new exception.
