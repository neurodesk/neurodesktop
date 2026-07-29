"""The Guacamole WAR / Jakarta EE migration downloads from archive.apache.org
must be resumable across retries.

Regression: build run 30478316312 failed because the Guacamole WAR download used
a non-resumable ``curl --retry`` (which restarts from byte 0 on every timeout).
archive.apache.org served ~15.5 of 15.6 MB before each 300 s ``--max-time``
cutoff (curl exit 28), so the transfer could never finish. The fix wraps the
downloads in the ``retry`` helper with ``curl --continue-at -`` so a timed-out
attempt resumes from the partial file instead of starting over.
"""

from testlib import repo_path


GUACAMOLE_WAR_URL = (
    "https://archive.apache.org/dist/guacamole/${GUACAMOLE_VERSION}/binary/"
    "guacamole-${GUACAMOLE_VERSION}.war"
)
MIGRATION_JAR_URL = (
    "https://archive.apache.org/dist/tomcat/jakartaee-migration/"
    "v${TOMCAT_MIGRATION_VERSION}/binaries/"
    "jakartaee-migration-${TOMCAT_MIGRATION_VERSION}-shaded.jar"
)


def test_guacamole_war_download_is_resumable_and_retried():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert GUACAMOLE_WAR_URL in dockerfile, "Guacamole WAR download URL was removed"
    assert MIGRATION_JAR_URL in dockerfile, (
        "Jakarta EE migration jar download URL was removed"
    )

    # The download must be wrapped in the `retry` helper (outer attempt loop) so
    # a single slow transfer does not fail the whole build.
    assert dockerfile.count("retry curl -fsSL") >= 2, (
        "both archive.apache.org downloads must run under the `retry` wrapper"
    )

    # Each curl must resume from the partial file so a timed-out attempt picks
    # up where it stopped instead of restarting from byte 0.
    assert dockerfile.count("--continue-at -") >= 2, (
        "both archive.apache.org downloads must use `curl --continue-at -` to "
        "resume after a timeout"
    )


def test_guacamole_war_download_does_not_use_bare_curl_retry():
    """The original non-resumable pattern must not be re-introduced.

    A bare ``curl --retry ... --max-time`` (without the `retry` wrapper and
    `--continue-at`) restarts from byte 0 on every timeout and cannot recover
    from a slow archive.apache.org mirror.
    """
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    # The Guacamole WAR / migration jar block is the only place these two
    # archive.apache.org URLs appear; neither may be fetched with a bare curl.
    for url_fragment in (
        "guacamole-${GUACAMOLE_VERSION}.war",
        "jakartaee-migration-${TOMCAT_MIGRATION_VERSION}-shaded.jar",
    ):
        idx = dockerfile.find(url_fragment)
        assert idx != -1, f"{url_fragment} download step was removed"
        # Walk back to the start of the RUN line that owns this URL.
        run_start = dockerfile.rfind("RUN ", 0, idx)
        block = dockerfile[run_start:idx]
        assert "retry curl -fsSL" in block, (
            f"{url_fragment} must be downloaded under the `retry` wrapper, not a "
            "bare curl"
        )
        assert "--continue-at -" in block, (
            f"{url_fragment} must use `curl --continue-at -` so its download "
            "resumes after a timeout"
        )
