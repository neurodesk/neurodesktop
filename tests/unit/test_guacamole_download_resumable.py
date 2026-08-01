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


def _download_command(dockerfile, url):
    """Return the single ``&&``-delimited shell command that fetches *url*.

    The slice runs from the command boundary that owns the URL (the nearest
    ``&&`` or the ``RUN`` that opens the statement) up to the next ``&&``, so one
    archive's ``retry``/``--continue-at`` flags cannot satisfy the check for a
    different archive's download.
    """
    idx = dockerfile.find(url)
    assert idx != -1, f"{url} download step was removed"
    start = max(dockerfile.rfind("&& ", 0, idx), dockerfile.rfind("RUN ", 0, idx))
    end = dockerfile.find("&&", idx)
    return dockerfile[start : end if end != -1 else len(dockerfile)]


def test_guacamole_war_download_is_resumable_and_retried():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    for url in (GUACAMOLE_WAR_URL, MIGRATION_JAR_URL):
        command = _download_command(dockerfile, url)
        assert "retry curl -fsSL" in command, (
            f"{url} must be downloaded under the `retry` wrapper so a single "
            "slow transfer does not fail the whole build"
        )
        assert "--continue-at -" in command, (
            f"{url} must use `curl --continue-at -` so a timed-out attempt "
            "resumes from the partial file instead of restarting from byte 0"
        )


def test_guacamole_war_download_does_not_use_bare_curl_retry():
    """The original non-resumable pattern must not be re-introduced.

    A bare ``curl`` (without the `retry` wrapper and ``--continue-at``) restarts
    from byte 0 on every timeout and cannot recover from a slow archive mirror.
    """
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    for url in (GUACAMOLE_WAR_URL, MIGRATION_JAR_URL):
        command = _download_command(dockerfile, url)
        assert "retry curl" in command, (
            f"{url} must not be fetched with a bare `curl`; use the `retry` "
            "wrapper with `--continue-at -` so the download resumes after a timeout"
        )
