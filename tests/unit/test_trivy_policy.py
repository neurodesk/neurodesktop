"""Packaging contracts for the header-only kernel CVE exceptions."""

from datetime import date

import yaml

from testlib import repo_path


HEADER_FINDINGS = (
    "CVE-2026-89530",
    "CVE-2026-89652",
    "CVE-2026-89659",
    "CVE-2026-89708",
    "CVE-2026-90037",
    "CVE-2026-90162",
    "CVE-2026-93221",
    "CVE-2026-98365",
    "CVE-2026-98571",
    "CVE-2026-98621",
    "CVE-2026-98725",
    "CVE-2026-98805",
)


def test_kernel_exceptions_apply_only_to_the_assessed_ubuntu_headers():
    policy = yaml.safe_load(repo_path(".trivyignore.yaml").read_text())
    findings = policy["vulnerabilities"]
    assert len({finding["id"] for finding in findings}) == len(findings)
    by_id = {finding["id"]: finding for finding in findings}
    for cve in HEADER_FINDINGS:
        finding = by_id[cve]
        expected = [
            "pkg:deb/ubuntu/linux-libc-dev@7.0.0-38.38",
            "pkg:deb/ubuntu/linux-libc-dev@7.0.0-42.42",
        ]
        if cve in (
            "CVE-2026-90162", "CVE-2026-98725", "CVE-2026-98805",
        ):
            expected.append("pkg:deb/ubuntu/linux-libc-dev@7.0.0-43.43")
        assert finding["purls"] == expected
        assert isinstance(finding["expired_at"], date)
        assert finding["expired_at"] <= date(2026, 11, 20)
        assert "host kernel" in finding["statement"]
    assert not policy.get("secrets")


def test_ipv4_tcp_exception_is_limited_to_inspected_header_version():
    policy = yaml.safe_load(repo_path(".trivyignore.yaml").read_text())
    finding = next(item for item in policy["vulnerabilities"]
                   if item["id"] == "CVE-2026-98432")
    assert finding["purls"] == [
        "pkg:deb/ubuntu/linux-libc-dev@7.0.0-42.42",
        "pkg:deb/ubuntu/linux-libc-dev@7.0.0-43.43",
    ]
    assert finding["expired_at"] == date(2026, 11, 20)
    assert "host kernel" in finding["statement"]
    assert "https://ubuntu.com/security/CVE-2026-98432" in finding["statement"]
