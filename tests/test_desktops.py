import subprocess
import os
import pytest

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_vnc_binaries_exist():
    """Verify VNC and RDP binaries are installed."""
    expected_cmds = [
        "vncserver",
        "Xvnc",
        "vncpasswd"
    ]
    for cmd in expected_cmds:
        code, _ = run_cmd(f"command -v {cmd}")
        assert code == 0, f"VNC command missing: {cmd}"

def test_rdp_binaries_exist():
    """Verify xrdp related binaries are installed."""
    expected_cmds = [
        "xrdp",
        "xrdp-sesman"
    ]
    for cmd in expected_cmds:
        code, _ = run_cmd(f"command -v {cmd}")
        assert code == 0, f"RDP command missing: {cmd}"

def test_guacamole_config_exists():
    """Verify Guacamole configuration exists."""
    assert os.path.exists("/etc/guacamole/guacd.conf"), "Guacamole guacd.conf missing"
    assert os.path.exists("/etc/guacamole/user-mapping-vnc.xml"), "Guacamole user-mapping missing"

def test_vnc_startup(tmp_path):
    """Start up a temporary VNC session to ensure it runs without crashing."""
    import time
    pwd_file = tmp_path / "vncpasswd"
    
    # Needs a vnc password
    code, output = run_cmd(f"printf 'password\\npassword\\n\\n' | vncpasswd {pwd_file}")
    assert code == 0, f"Could not generate vncpasswd: {output}"
    
    # Pick a random display port like :99
    # Use the container's default xstartup instead of ~/.vnc/xstartup because restore_home_defaults hasn't run
    code, output = run_cmd(f"USER=jovyan vncserver -xstartup /opt/jovyan_defaults/.vnc/xstartup -rfbauth {pwd_file} :99")
    assert code == 0, f"VNC server failed to start: {output}"
    
    # Give it a second
    time.sleep(2)
    
    # Check if the process Xtigervnc or vncserver is running for display :99
    code, output = run_cmd("ps aux | grep -E 'Xtigervnc.*:99'")
    assert code == 0, f"Xtigervnc :99 process is not running. VNC startup crashed. {output}"
    
    # Clean up
    run_cmd("vncserver -kill :99")
