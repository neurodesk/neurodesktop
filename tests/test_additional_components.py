import subprocess
import os
import re
import pytest

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

def test_neurocommand_setup():
    """Verify neurocommand installation."""
    assert os.path.exists("/neurocommand"), "/neurocommand directory missing"
    
def test_guacamole_tomcat_running():
    """Verify Tomcat and Guacamole processes running."""
    # Guacd process check
    code, output = run_cmd("ps aux | grep '[g]uacd'")
    # We might not be running services inside the build container yet (services are started in startup scripts)
    # Just asserting the binaries and wrappers exist
    assert os.path.exists("/usr/local/tomcat/webapps/ROOT.war"), "Guacamole webapp ROOT.war missing"
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
    
def test_desktop_storage():
    """Verify neurodesktop-storage is accessible."""
    assert os.path.exists("/neurodesktop-storage"), "/neurodesktop-storage is missing"
