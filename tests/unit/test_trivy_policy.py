"""Packaging contract for image-scan exceptions."""

from datetime import date, timedelta

import yaml

from testlib import repo_path

# Longest interval an exception may run before it must be reassessed.
MAX_EXCEPTION_DAYS = 90


def load_policy():
    return yaml.safe_load(repo_path(".trivyignore.yaml").read_text(encoding="utf-8"))


def test_policy_is_a_parseable_trivy_ignore_file():
    policy = load_policy()

    assert isinstance(policy["vulnerabilities"], list)
    assert not policy.get("secrets")
    assert not policy.get("misconfigurations")
    assert not policy.get("licenses")


def test_every_exception_is_scoped_to_packages_and_expires():
    findings = load_policy()["vulnerabilities"]

    assert len({finding["id"] for finding in findings}) == len(findings)
    latest_expiry = date.today() + timedelta(days=MAX_EXCEPTION_DAYS)
    for finding in findings:
        assert finding["purls"], finding["id"]
        assert all("@" in purl for purl in finding["purls"]), finding["id"]
        assert isinstance(finding["expired_at"], date), finding["id"]
        assert finding["expired_at"] <= latest_expiry, finding["id"]
        assert finding["statement"].strip(), finding["id"]


def test_resolved_kernel_header_exceptions_are_not_reintroduced():
    """Ubuntu fixed these in linux-libc-dev 7.0.0-43.43 or earlier."""
    excepted = {finding["id"] for finding in load_policy()["vulnerabilities"]}
    resolved = {
        f"CVE-2026-{number}"
        for number in (
            53215, 53398, 63940, 64535, 64564, 74394, 89530, 89652, 89659,
            89708, 90037, 90162, 93221, 98365, 98432, 98571, 98621, 98725,
            98805,
        )
    }

    assert not excepted & resolved


def test_unfixed_kernel_header_findings_are_scoped_to_the_affected_headers():
    """The 24 November scan findings must not hide fixed or future packages."""
    findings = {finding["id"]: finding for finding in load_policy()["vulnerabilities"]}
    incident_cves = {
        "CVE-2026-99343",
        "CVE-2026-99344",
        "CVE-2026-99355",
        "CVE-2026-99362",
        "CVE-2026-99366",
        "CVE-2026-99441",
        "CVE-2026-99460",
    }

    assert incident_cves <= findings.keys()
    for cve in incident_cves:
        assert findings[cve]["purls"] == [
            "pkg:deb/ubuntu/linux-libc-dev@7.0.0-44.44"
        ]
