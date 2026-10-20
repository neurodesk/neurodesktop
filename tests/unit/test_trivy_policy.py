"""Packaging contracts for the header-only kernel CVE exceptions."""

from datetime import date

import yaml

from testlib import repo_path


# Both scans in Build neurodesktop run 39464164441 reported these findings.
HEADER_FINDINGS = (
    "CVE-2026-89530",
    "CVE-2026-89652",
    "CVE-2026-89659",
    "CVE-2026-89708",
    "CVE-2026-90037",
    "CVE-2026-90162",
    "CVE-2026-93221",
    "CVE-2026-98365",
)


def test_kernel_exceptions_apply_only_to_the_assessed_ubuntu_headers():
    policy = yaml.safe_load(repo_path(".trivyignore.yaml").read_text())
    findings = policy["vulnerabilities"]
    assert len({finding["id"] for finding in findings}) == len(findings)
    by_id = {finding["id"]: finding for finding in findings}
    for cve in HEADER_FINDINGS:
        finding = by_id[cve]
        assert finding["purls"] == ["pkg:deb/ubuntu/linux-libc-dev@7.0.0-38.38"]
        assert isinstance(finding["expired_at"], date)
        assert finding["expired_at"] <= date(2026, 11, 20)
        assert "host kernel" in finding["statement"]
    assert not policy.get("secrets")
