import errno
import json
import os
import subprocess
import time
from pathlib import Path
import pytest

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def test_coding_agent_claude():
    """Verify Claude agent wrapper is available."""
    code, output = run_cmd("command -v claude")
    assert code == 0, f"Claude agent command missing: {output}"

def test_coding_agent_codex():
    """Verify Codex agent wrapper is available."""
    code, output = run_cmd("command -v codex")
    assert code == 0, f"Codex agent command missing: {output}"

def test_coding_agent_opencode():
    """Verify OpenCode agent wrapper is available."""
    code, output = run_cmd("command -v opencode")
    assert code == 0, f"OpenCode agent command missing: {output}"

def run_pty_command(args, input_text, cwd, env, timeout=15):
    """Run an interactive wrapper under a PTY and collect combined output."""
    import pty
    import select

    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    output = bytearray()
    deadline = time.monotonic() + timeout

    try:
        if input_text:
            os.write(master_fd, input_text.encode("utf-8"))

        while True:
            if time.monotonic() > deadline:
                process.kill()
                raise subprocess.TimeoutExpired(
                    args, timeout, output=output.decode("utf-8", errors="replace")
                )

            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)

            if process.poll() is not None:
                while True:
                    readable, _, _ = select.select([master_fd], [], [], 0)
                    if not readable:
                        break
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output.extend(chunk)
                break

        return process.wait(timeout=1), output.decode("utf-8", errors="replace")
    finally:
        os.close(master_fd)
        if process.poll() is None:
            process.kill()

def make_opencode_litellm_wrapper(tmp_path):
    """Create a testable OpenCode wrapper with fake LiteLLM responses."""
    wrapper_path = Path("/usr/local/sbin/opencode")
    if not wrapper_path.exists():
        pytest.skip("OpenCode wrapper not installed in this environment")

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()

    default_config = tmp_path / "opencode-default.json"
    default_config.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": "neurodesk/devstral-small-2",
                "provider": {
                    "neurodesk": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Neurodesk vLLM",
                        "options": {
                            "baseURL": "https://llm.neurodesk.org/v1",
                            "apiKey": "{env:NEURODESK_API_KEY}",
                        },
                        "models": {
                            "devstral-small-2": {
                                "name": "Devstral Small 2 24B",
                                "limit": {"context": 131000, "output": 8192},
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    fake_curl = fake_bin_dir / "curl"
    fake_curl.write_text(
        """#!/bin/sh
outfile=""
auth=""
url=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            outfile="$2"
            shift 2
            ;;
        -H)
            case "$2" in
                Authorization:*) auth="$2" ;;
            esac
            shift 2
            ;;
        http://*|https://*)
            url="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [ -z "$outfile" ]; then
    outfile="/dev/null"
fi

case "$url" in
    *llm.neurodesk.org*/models)
        case "$auth" in
            "Authorization: Bearer neurodesk-test-key"|"Authorization: Bearer new-neurodesk-key")
                printf '%s' '{"data":[{"id":"model-alpha"},{"id":"openai/gpt-4.1-mini"}]}' > "$outfile"
                printf '200'
                ;;
            *)
                printf '%s' '{"error":{"message":"Authentication Error, No api key passed in."}}' > "$outfile"
                printf '401'
                ;;
        esac
        ;;
    *llm.jetstream-cloud.org*)
        printf '%s' '{"error":"unavailable"}' > "$outfile"
        printf '503'
        ;;
    *127.0.0.1:11434/api/tags*)
        printf '%s' '{}' > "$outfile"
        printf '000'
        ;;
    *)
        printf '%s' '{}' > "$outfile"
        printf '000'
        ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    fake_opencode = tmp_path / "fake-opencode"
    fake_opencode.write_text("#!/bin/sh\necho \"FAKE_OPENCODE:$*\"\n", encoding="utf-8")
    fake_opencode.chmod(0o755)

    test_wrapper = tmp_path / "opencode-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace(
        'OPENCODE_DEFAULT_CONFIG_FILE="/opt/jovyan_defaults/.config/opencode/opencode.json"',
        f'OPENCODE_DEFAULT_CONFIG_FILE="{default_config}"',
    )
    wrapper_contents = wrapper_contents.replace("/usr/bin/opencode", str(fake_opencode))
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    (tmp_path / "AGENTS.md").write_text("test", encoding="utf-8")

    env = {
        **os.environ,
        "HOME": str(home_dir),
        "PATH": f"{fake_bin_dir}:{os.environ['PATH']}",
        "TERM": "xterm",
    }
    env.pop("NEURODESK_API_KEY", None)
    env.pop("OPENCODE_MODEL_PROFILE", None)

    return test_wrapper, home_dir, env

def test_opencode_shows_litellm_models_after_api_key_creation(tmp_path):
    """Verify first-time Neurodesk key setup shows LiteLLM models and updates OpenCode."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n2\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Open https://llm.neurodesk.org/ui/ and create an account" in output
    assert "Available llm.neurodesk.org LiteLLM models:" in output
    assert "1) model-alpha" in output
    assert "2) openai/gpt-4.1-mini" in output
    assert "OpenCode default model set to neurodesk/openai/gpt-4.1-mini." in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    neurodesk_provider = user_config["provider"]["neurodesk"]
    assert user_config["model"] == "neurodesk/openai/gpt-4.1-mini"
    assert neurodesk_provider["name"] == "Neurodesk LiteLLM"
    assert list(neurodesk_provider["models"]) == ["model-alpha", "openai/gpt-4.1-mini"]

def test_opencode_rejected_neurodesk_key_points_to_litellm_ui(tmp_path):
    """Verify rejected Neurodesk keys ask users to generate a replacement via LiteLLM UI."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["NEURODESK_API_KEY"] = "expired-neurodesk-key"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "new-neurodesk-key\n1\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert (
        "llm.neurodesk.org: running, but the current NEURODESK_API_KEY is rejected (HTTP 401)"
        in output
    )
    assert (
        "Please generate a new API key at https://llm.neurodesk.org/ui/ and paste it below."
        in output
    )
    assert "Available llm.neurodesk.org LiteLLM models:" in output
    assert "OpenCode default model set to neurodesk/model-alpha." in output

    bashrc = (home_dir / ".bashrc").read_text(encoding="utf-8")
    assert "new-neurodesk-key" in bashrc
    assert "expired-neurodesk-key" not in bashrc

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    assert user_config["model"] == "neurodesk/model-alpha"

def test_codex_yolo_no_full_auto(tmp_path):
    """Verify Codex wrapper does not combine --yolo with --full-auto."""
    wrapper_path = Path("/usr/local/sbin/codex")
    if not wrapper_path.exists():
        pytest.skip("Codex wrapper not installed in this environment")

    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do\n"
        "  echo \"ARG:${arg}\"\n"
        "done\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    test_wrapper = tmp_path / "codex-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace("/usr/bin/codex", str(fake_codex))
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    (tmp_path / "AGENTS.md").write_text("test", encoding="utf-8")
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [str(test_wrapper), "--yolo"],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path / "home")},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "ARG:--yolo" in result.stdout
    assert "ARG:--full-auto" not in result.stdout

def test_codex_default_full_auto(tmp_path):
    """Verify Codex wrapper keeps full-auto default when no yolo/bypass flag is passed."""
    wrapper_path = Path("/usr/local/sbin/codex")
    if not wrapper_path.exists():
        pytest.skip("Codex wrapper not installed in this environment")

    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do\n"
        "  echo \"ARG:${arg}\"\n"
        "done\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    test_wrapper = tmp_path / "codex-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace("/usr/bin/codex", str(fake_codex))
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    (tmp_path / "AGENTS.md").write_text("test", encoding="utf-8")
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path / "home")},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "ARG:--full-auto" in result.stdout
    assert "ARG:--version" in result.stdout

def test_claude_replaces_dangling_symlink(tmp_path):
    """Verify Claude wrapper restores binary when ~/.local/bin/claude is a dangling symlink."""
    wrapper_path = Path("/usr/local/sbin/claude")
    if not wrapper_path.exists():
        pytest.skip("Claude wrapper not installed in this environment")

    home_dir = tmp_path / "home"
    bin_dir = home_dir / ".local" / "bin"
    bin_dir.mkdir(parents=True)

    claude_link = bin_dir / "claude"
    claude_link.symlink_to(home_dir / "missing" / "claude")

    fake_default_claude = tmp_path / "default-claude"
    fake_default_claude.write_text(
        "#!/bin/sh\n"
        "echo \"$0 $@\"\n",
        encoding="utf-8",
    )
    fake_default_claude.chmod(0o755)

    test_wrapper = tmp_path / "claude-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace(
        'DEFAULT_CLAUDE_BIN="/opt/jovyan_defaults/.local/bin/claude"',
        f'DEFAULT_CLAUDE_BIN="{fake_default_claude}"',
    )
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home_dir)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert (bin_dir / "claude").exists(), "Claude binary was not restored"
    assert not (bin_dir / "claude").is_symlink(), "Dangling symlink was not replaced"
    assert os.access(bin_dir / "claude", os.X_OK), "Restored binary is not executable"
    assert "--allow-dangerously-skip-permissions --version" in result.stdout
