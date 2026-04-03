import subprocess
import os
import re
import pytest


NOTEBOOK_SUDOERS_PATH = "/etc/sudoers.d/notebook"


def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_cvmfs_mounts():
    """Verify CVMFS mounts and configurations are properly loaded."""
    # Since CVMFS might be disabled during tests via environment variable CVMFS_DISABLE
    # Check if disabled, if so skip or just check for the directory presence.
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    
    if cvmfs_disable in ["true", "1"]:
        pytest.skip("CVMFS is disabled in this environment (CVMFS_DISABLE=true)")
        
    assert os.path.isdir("/cvmfs"), "The /cvmfs directory is missing"
    # Testing for actual mount would require running cvmfs_config probe or stat
    # Only test if proxy is correctly set in default.local
    if os.path.exists("/etc/cvmfs/default.local"):
        with open("/etc/cvmfs/default.local") as f:
            content = f.read()
            assert "CVMFS_HTTP_PROXY" in content, "CVMFS_HTTP_PROXY configuration missing"


def test_cvmfs_runtime_components_installed():
    """Verify the final image keeps the CVMFS runtime packages and helpers."""
    code, _ = run_cmd("dpkg-query -W cvmfs autofs uuid-dev >/dev/null 2>&1")
    assert code == 0, "cvmfs, autofs, and uuid-dev must remain installed in the runtime image"

    assert os.path.exists("/etc/init.d/autofs"), "autofs init script missing"

    code, output = run_cmd("command -v cvmfs_config")
    assert code == 0, f"cvmfs_config not found in PATH: {output}"


def test_neurocommand_setup():
    """Verify neurocommand installation."""
    assert os.path.exists("/neurocommand"), "/neurocommand directory missing"
    
def test_guacamole_webapp_files_exist():
    """Verify the Guacamole web application was unpacked into Tomcat."""
    assert os.path.exists("/usr/local/tomcat/webapps/ROOT/WEB-INF/web.xml"), "Guacamole webapp missing (expected extracted ROOT directory)"
    assert os.path.exists("/usr/local/tomcat/bin/startup.sh"), "Tomcat startup script missing"


def test_tomcat_request_header_limit_hardened():
    """Verify Tomcat accepts larger request headers for browser compatibility."""
    server_xml_path = "/usr/local/tomcat/conf/server.xml"
    assert os.path.exists(server_xml_path), "Tomcat server.xml missing"

    with open(server_xml_path, "r", encoding="utf-8") as server_xml_file:
        server_xml = server_xml_file.read()

    match = re.search(r'maxHttpRequestHeaderSize="(\d+)"', server_xml)
    assert match is not None, "Tomcat maxHttpRequestHeaderSize is not configured"
    assert int(match.group(1)) >= 65536, "Tomcat maxHttpRequestHeaderSize should be at least 65536"


def test_tomcat_session_cookie_path():
    """Verify context.xml sets sessionCookiePath to prevent duplicate path-scoped cookies."""
    context_xml_path = "/usr/local/tomcat/conf/context.xml"
    assert os.path.exists(context_xml_path), "Tomcat context.xml missing"

    with open(context_xml_path, "r", encoding="utf-8") as f:
        context_xml = f.read()

    assert 'sessionCookiePath="/"' in context_xml, \
        "context.xml must set sessionCookiePath=\"/\" to prevent cookie accumulation"
    assert "Rfc6265CookieProcessor" in context_xml, \
        "context.xml must configure Rfc6265CookieProcessor"
    assert 'sameSiteCookies="Lax"' in context_xml, \
        "CookieProcessor must set sameSiteCookies to Lax"


def test_tomcat_session_cookie_max_age():
    """Verify Guacamole's web.xml sets Max-Age on session cookie so browsers auto-expire it."""
    guac_web_xml_path = "/usr/local/tomcat/webapps/ROOT/WEB-INF/web.xml"
    assert os.path.exists(guac_web_xml_path), \
        "Guacamole web.xml missing - ROOT.war should be extracted during build"

    with open(guac_web_xml_path, "r", encoding="utf-8") as f:
        web_xml = f.read()

    assert "<cookie-config>" in web_xml, \
        "Guacamole web.xml must contain <cookie-config> for session cookie settings"
    match = re.search(r"<max-age>(\d+)</max-age>", web_xml)
    assert match is not None, "Guacamole web.xml must set <max-age> in <cookie-config>"
    max_age = int(match.group(1))
    assert 0 < max_age <= 86400, \
        f"Session cookie max-age should be between 1 and 86400 seconds, got {max_age}"
    assert "<http-only>true</http-only>" in web_xml, \
        "Session cookie must be HttpOnly"


def test_desktop_storage():
    """Verify neurodesktop-storage is accessible."""
    assert os.path.exists("/neurodesktop-storage"), "/neurodesktop-storage is missing"


def test_build_only_toolchain_removed():
    """Verify the broad build-only toolchain is not retained in the runtime image."""
    code, output = run_cmd("dpkg-query -W -f='${Status}' build-essential 2>/dev/null")
    assert code != 0, (
        "build-essential should be removed after build-only packages are purged. "
        f"Found: {output}"
    )


def test_grant_sudo_no_disables_passwordless_sudo():
    """Verify GRANT_SUDO=no removes Neurodesktop's managed passwordless sudo rule."""
    nb_user = os.environ.get("NB_USER", "jovyan")
    grant_sudo = os.environ.get("GRANT_SUDO", "").lower()

    if grant_sudo not in {"no", "n", "false", "0"}:
        pytest.skip("This assertion only applies when the container starts with GRANT_SUDO=no")

    assert not os.path.exists(NOTEBOOK_SUDOERS_PATH), (
        "GRANT_SUDO=no should remove Neurodesktop's managed passwordless sudo "
        f"rule for {nb_user}, but {NOTEBOOK_SUDOERS_PATH} still exists."
    )

    code, current_user = run_cmd("id -un")
    assert code == 0, f"Failed to determine current user: {current_user}"

    if os.geteuid() == 0:
        code, output = run_cmd(f"su -s /bin/bash -c 'sudo -n true' {nb_user}")
    elif current_user == nb_user:
        code, output = run_cmd("sudo -n true")
    else:
        pytest.skip(
            f"Test requires root or NB_USER ({nb_user}); current user is {current_user}"
        )

    assert code != 0, (
        "GRANT_SUDO=no should leave passwordless sudo unavailable unless the "
        f"runtime grants it separately. Output: {output}"
    )
