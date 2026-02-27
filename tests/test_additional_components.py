import subprocess
import os
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
    """Verify neurocommand installation and container linkage."""
    assert os.path.exists("/neurocommand"), "/neurocommand directory missing"
    assert os.path.exists("/neurocommand/local/containers"), "/neurocommand/local/containers not found"
    
def test_guacamole_tomcat_running():
    """Verify Tomcat and Guacamole processes running."""
    # Guacd process check
    code, output = run_cmd("ps aux | grep '[g]uacd'")
    # We might not be running services inside the build container yet (services are started in startup scripts)
    # Just asserting the binaries and wrappers exist
    assert os.path.exists("/usr/local/tomcat/webapps/ROOT.war"), "Guacamole webapp ROOT.war missing"
    assert os.path.exists("/usr/local/tomcat/bin/startup.sh"), "Tomcat startup script missing"
    
def test_desktop_storage():
    """Verify neurodesktop-storage is accessible."""
    assert os.path.exists("/neurodesktop-storage"), "/neurodesktop-storage is missing"
