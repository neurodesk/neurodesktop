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

def opencode_wrapper_path():
    """Return the installed OpenCode wrapper path, or a test override."""
    return Path(
        os.environ.get("NEURODESKTOP_TEST_OPENCODE_WRAPPER", "/usr/local/sbin/opencode")
    )

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
    wrapper_override = os.environ.get("NEURODESKTOP_TEST_OPENCODE_WRAPPER")
    if wrapper_override:
        wrapper_path = Path(wrapper_override)
        assert wrapper_path.exists(), f"OpenCode wrapper missing: {wrapper_path}"
        assert os.access(wrapper_path, os.X_OK), (
            f"OpenCode wrapper not executable: {wrapper_path}"
        )
        return

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
    wrapper_path = opencode_wrapper_path()
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
                "model": "neurodesk/gpt-oss",
                "provider": {
                    "neurodesk": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Neurodesk vLLM",
                        "options": {
                            "baseURL": "https://llm.neurodesk.org/openai",
                            "apiKey": "{env:NEURODESK_API_KEY}",
                        },
                        "models": {
                            "gpt-oss": {
                                "name": "gpt-oss",
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
    https://llm.neurodesk.org/openai/models)
        if [ "${FAKE_NEURODESK_MODELS_HTTP:-}" = "404" ] && [ -z "$auth" ]; then
            printf '%s' '{"detail":"Not Found"}' > "$outfile"
            printf '404'
            exit 0
        fi
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
    *llm.neurodesk.org*)
        printf '%s' '{"error":{"message":"unexpected Neurodesk URL"}}' > "$outfile"
        printf '302'
        ;;
    *llm.jetstream-cloud.org*)
        printf '%s' '{"error":"unavailable"}' > "$outfile"
        printf '503'
        ;;
    *127.0.0.1:11434/api/tags*)
        if [ "${FAKE_OLLAMA_MODELS:-}" = "1" ]; then
            printf '%s' '{"models":[{"name":"local-model:latest"}]}' > "$outfile"
            printf '200'
        else
            printf '%s' '{}' > "$outfile"
            printf '000'
        fi
        ;;
    *127.0.0.1:9/api/tags*)
        if [ "${FAKE_OLLAMA_MODELS:-}" = "1" ]; then
            printf '%s' '{"models":[{"name":"local-model:latest"}]}' > "$outfile"
            printf '200'
        else
            printf '%s' '{}' > "$outfile"
            printf '000'
        fi
        ;;
    *host.docker.internal:11434/api/tags*)
        if [ "${FAKE_OLLAMA_MODELS:-}" = "1" ]; then
            printf '%s' '{"models":[{"name":"local-model:latest"}]}' > "$outfile"
            printf '200'
        else
            printf '%s' '{}' > "$outfile"
            printf '000'
        fi
        ;;
    *api/tags*)
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
        "NO_COLOR": "1",
        "TERM": "xterm",
    }
    env.pop("NEURODESK_API_KEY", None)
    env.pop("OPENCODE_MODEL_PROFILE", None)
    env.pop("BR_MCP_TOKEN", None)

    return test_wrapper, home_dir, env

def test_opencode_shows_litellm_models_after_api_key_creation(tmp_path):
    """Verify first-time Neurodesk key setup shows LiteLLM models and updates OpenCode."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n2\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "OpenCode model setup" in output
    assert "Provider status" in output
    assert "llm.neurodesk.org  needs API key" in output
    assert "Checking llm.neurodesk.org API" not in output
    assert (
        "Set OPENCODE_STARTUP_VERBOSE=1 to show endpoint probe details."
        in output
    )
    assert "Open https://llm.neurodesk.org and create an account" in output
    assert "Click your user avatar -> Settings -> Account." in output
    assert (
        'Scroll to the "API Keys" section, then click "Create new secret key" / "Show"'
        in output
    )
    assert "Paste Neurodesk API key (input hidden, press Enter when done):" in output
    assert "API key verified with llm.neurodesk.org." in output
    assert "Available llm.neurodesk.org models:" in output
    assert "1) model-alpha" in output
    assert "2) openai/gpt-4.1-mini" in output
    assert "Enter model number [1-2]:" in output
    assert "Choose the default model [" not in output
    assert "OpenCode default model set to neurodesk/openai/gpt-4.1-mini." in output
    assert "Brain Researcher MCP server setup" in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    neurodesk_provider = user_config["provider"]["neurodesk"]
    assert user_config["model"] == "neurodesk/openai/gpt-4.1-mini"
    assert neurodesk_provider["name"] == "Neurodesk LLMs"
    assert (
        neurodesk_provider["options"]["baseURL"]
        == "https://llm.neurodesk.org/openai"
    )
    assert list(neurodesk_provider["models"]) == ["model-alpha", "openai/gpt-4.1-mini"]

def test_opencode_neurodesk_setup_choice_does_not_claim_known_model(tmp_path):
    """Verify unauthenticated Neurodesk is shown as key setup, not a known model."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["FAKE_OLLAMA_MODELS"] = "1"
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "2\nneurodesk-test-key\n2\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Provider status" in output
    assert "Local Ollama       available: 1 model" in output
    assert "llm.neurodesk.org  needs API key" in output
    assert "Choose a default OpenCode model." in output
    assert "Local Ollama" in output
    assert "1) local-model:latest" in output
    assert "llm.neurodesk.org" in output
    assert "2) Set up API key to list models" in output
    assert "2) gpt-oss (requires API key setup)" not in output
    assert "API key verified with llm.neurodesk.org." in output
    assert "Available llm.neurodesk.org models:" in output
    assert "OpenCode default model set to neurodesk/openai/gpt-4.1-mini." in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    assert user_config["model"] == "neurodesk/openai/gpt-4.1-mini"

def test_opencode_neurodesk_404_models_response_still_prompts_for_key(tmp_path):
    """Verify unauthenticated Neurodesk API 404 still allows key setup."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["FAKE_NEURODESK_MODELS_HTTP"] = "404"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "OpenCode model setup" in output
    assert "Provider status" in output
    assert "llm.neurodesk.org  needs API key" in output
    assert "Checking llm.neurodesk.org API" not in output
    assert "OpenAI-compatible API unavailable" not in output
    assert "Paste Neurodesk API key (input hidden, press Enter when done):" in output
    assert "API key verified with llm.neurodesk.org." in output
    assert "Available llm.neurodesk.org models:" in output
    assert "Enter model number [1-2]:" in output
    assert "OpenCode default model set to neurodesk/model-alpha." in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    assert user_config["model"] == "neurodesk/model-alpha"

def test_opencode_startup_verbose_shows_probe_details(tmp_path):
    """Verify verbose startup keeps detailed provider probe output available."""
    test_wrapper, _home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["OPENCODE_STARTUP_VERBOSE"] = "1"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Checking Jetstream model API (gpt-oss-120b)..." in output
    assert (
        "Checking llm.neurodesk.org API at https://llm.neurodesk.org/openai/models..."
        in output
    )
    assert "Provider probe details" in output
    assert "llm.neurodesk.org  running, API key required (HTTP 401)" in output
    assert "Set OPENCODE_STARTUP_VERBOSE=1" not in output

def test_opencode_rejected_neurodesk_key_points_to_litellm_site(tmp_path):
    """Verify rejected Neurodesk keys ask users to generate a replacement via LiteLLM."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["NEURODESK_API_KEY"] = "expired-neurodesk-key"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "new-neurodesk-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "llm.neurodesk.org  needs API key: current key rejected" in output
    assert (
        "Please generate a new API key at https://llm.neurodesk.org and paste it below."
        in output
    )
    assert "Click your user avatar -> Settings -> Account." in output
    assert "Paste Neurodesk API key (input hidden, press Enter when done):" in output
    assert "API key verified with llm.neurodesk.org." in output
    assert "Rechecking llm.neurodesk.org with the new API key..." in output
    assert "llm.neurodesk.org  available: 2 models" in output
    assert "Choose a default OpenCode model." in output
    assert "Working models detected:" not in output
    assert "llm.neurodesk.org" in output
    assert "1) model-alpha" in output
    assert "2) openai/gpt-4.1-mini" in output
    assert "Enter model number [1-2]:" in output
    assert "llm.neurodesk.org / gpt-oss (requires a valid API key)" not in output
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
    assert (
        user_config["provider"]["neurodesk"]["options"]["baseURL"]
        == "https://llm.neurodesk.org/openai"
    )

def test_opencode_rejected_neurodesk_key_refreshes_before_mixed_model_picker(tmp_path):
    """Verify a rejected Neurodesk key is refreshed before showing mixed providers."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["NEURODESK_API_KEY"] = "expired-neurodesk-key"
    env["FAKE_OLLAMA_MODELS"] = "1"
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "new-neurodesk-key\n3\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert (
        output.index(
            "Please generate a new API key at https://llm.neurodesk.org and paste it below."
        )
        < output.index("Choose a default OpenCode model.")
    )
    assert "API key verified with llm.neurodesk.org." in output
    assert "Local Ollama" in output
    assert "llm.neurodesk.org" in output
    assert "1) local-model:latest" in output
    assert "2) model-alpha" in output
    assert "3) openai/gpt-4.1-mini" in output
    assert "Local Ollama / local-model:latest" not in output
    assert (
        "Tip: set OPENCODE_MODEL_PROFILE=ollama, neurodesk, jetstream, or provider/model"
        in output
    )
    assert "Enter model number [1-3]:" in output
    assert "Choose the default model [" not in output
    assert "llm.neurodesk.org / gpt-oss (requires a valid API key)" not in output
    assert "OpenCode default model set to neurodesk/openai/gpt-4.1-mini." in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    neurodesk_provider = user_config["provider"]["neurodesk"]
    assert user_config["model"] == "neurodesk/openai/gpt-4.1-mini"
    assert (
        neurodesk_provider["options"]["baseURL"]
        == "https://llm.neurodesk.org/openai"
    )
    assert list(neurodesk_provider["models"]) == ["model-alpha", "openai/gpt-4.1-mini"]

def test_opencode_reprompts_when_pasted_neurodesk_key_is_rejected(tmp_path):
    """Verify first-time Neurodesk setup retries until a pasted key is accepted."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "wrong-neurodesk-key\nneurodesk-test-key\n2\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert (
        "That API key was rejected by llm.neurodesk.org. Please paste a correct key."
        in output
    )
    assert output.count(
        "Paste Neurodesk API key (input hidden, press Enter when done):"
    ) == 2
    assert "API key verified with llm.neurodesk.org." in output
    assert "Available llm.neurodesk.org models:" in output
    assert "OpenCode default model set to neurodesk/openai/gpt-4.1-mini." in output

    bashrc = (home_dir / ".bashrc").read_text(encoding="utf-8")
    assert "neurodesk-test-key" in bashrc
    assert "wrong-neurodesk-key" not in bashrc

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
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("BR_MCP_TOKEN", None)

    result = subprocess.run(
        [str(test_wrapper), "--yolo"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "ARG:--yolo" in result.stdout
    assert "ARG:--full-auto" not in result.stdout


def test_codex_default_no_approval_prompts_without_managed_sandbox(tmp_path):
    """Verify Codex wrapper defaults to no approval prompts and no managed sandbox."""
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
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("BR_MCP_TOKEN", None)

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "ARG:--ask-for-approval" in result.stdout
    assert "ARG:never" in result.stdout
    assert "ARG:--sandbox" in result.stdout
    assert "ARG:danger-full-access" in result.stdout
    assert "ARG:--full-auto" not in result.stdout
    assert "ARG:--version" in result.stdout


def test_codex_respects_explicit_approval_and_sandbox_flags(tmp_path):
    """Verify Codex wrapper does not override explicit approval/sandbox flags."""
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
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("BR_MCP_TOKEN", None)

    result = subprocess.run(
        [str(test_wrapper), "--ask-for-approval", "on-request", "--sandbox=read-only"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert result.stdout.count("ARG:--ask-for-approval") == 1
    assert "ARG:on-request" in result.stdout
    assert "ARG:--sandbox=read-only" in result.stdout
    assert "ARG:never" not in result.stdout
    assert "ARG:danger-full-access" not in result.stdout

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

    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("BR_MCP_TOKEN", None)

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert (bin_dir / "claude").exists(), "Claude binary was not restored"
    assert not (bin_dir / "claude").is_symlink(), "Dangling symlink was not replaced"
    assert os.access(bin_dir / "claude", os.X_OK), "Restored binary is not executable"
    assert "--allow-dangerously-skip-permissions" in result.stdout
    assert "--version" in result.stdout


def test_opencode_brain_researcher_mcp_setup_accept(tmp_path):
    """Verify the OpenCode wrapper prompts for and persists a Brain Researcher MCP token."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n2\ny\nbr-mcp-test-token\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Brain Researcher MCP server setup" in output
    assert (
        "Enable the Brain Researcher MCP server for Claude Code, Codex, and OpenCode?"
        in output
    )
    assert "https://brain-researcher.com/settings" in output
    assert (
        "Paste Brain Researcher MCP token (input hidden, press Enter when done):"
        in output
    )
    assert "Brain Researcher MCP token received (input hidden)." in output
    assert "Saved BR_MCP_TOKEN" in output

    bashrc = (home_dir / ".bashrc").read_text(encoding="utf-8")
    assert "BR_MCP_TOKEN='br-mcp-test-token'" in bashrc
    assert "BR_MCP_DECLINED" not in bashrc

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    mcp_cfg = user_config.get("mcp", {})
    assert "brain-researcher" in mcp_cfg
    brain_cfg = mcp_cfg["brain-researcher"]
    assert brain_cfg["type"] == "remote"
    assert brain_cfg["url"] == "https://brain-researcher.com/mcp"
    assert brain_cfg["enabled"] is True
    assert (
        brain_cfg["headers"]["Authorization"] == "Bearer {env:BR_MCP_TOKEN}"
    )


def test_opencode_brain_researcher_mcp_setup_decline(tmp_path):
    """Verify declining the Brain Researcher prompt records a decline marker."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n2\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Brain Researcher MCP server setup" in output
    assert "Skipping Brain Researcher MCP setup" in output

    bashrc = (home_dir / ".bashrc").read_text(encoding="utf-8")
    assert "BR_MCP_DECLINED" in bashrc
    assert "BR_MCP_TOKEN" not in bashrc

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    mcp_cfg = user_config.get("mcp", {})
    if "brain-researcher" in mcp_cfg:
        assert mcp_cfg["brain-researcher"].get("enabled") is False


def test_opencode_brain_researcher_prompt_skipped_when_token_exists(tmp_path):
    """Verify the prompt is skipped when BR_MCP_TOKEN is already exported."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["BR_MCP_TOKEN"] = "preexisting-token"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n2\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Brain Researcher MCP server setup" not in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    mcp_cfg = user_config.get("mcp", {})
    assert mcp_cfg.get("brain-researcher", {}).get("enabled") is True


def _make_claude_wrapper_with_token(tmp_path, bashrc_contents, env_token=None):
    wrapper_path = Path("/usr/local/sbin/claude")
    if not wrapper_path.exists():
        pytest.skip("Claude wrapper not installed in this environment")

    home_dir = tmp_path / "home"
    bin_dir = home_dir / ".local" / "bin"
    bin_dir.mkdir(parents=True)

    fake_default_claude = tmp_path / "default-claude"
    fake_default_claude.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do echo \"ARG:${arg}\"; done\n",
        encoding="utf-8",
    )
    fake_default_claude.chmod(0o755)

    mcp_config_file = tmp_path / "claude-mcp-config.json"
    mcp_config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "brain-researcher": {
                        "type": "http",
                        "url": "https://brain-researcher.com/mcp",
                        "headers": {
                            "Authorization": "Bearer ${BR_MCP_TOKEN}"
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    test_wrapper = tmp_path / "claude-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace(
        'DEFAULT_CLAUDE_BIN="/opt/jovyan_defaults/.local/bin/claude"',
        f'DEFAULT_CLAUDE_BIN="{fake_default_claude}"',
    )
    wrapper_contents = wrapper_contents.replace(
        'CLAUDE_DEFAULT_MCP_CONFIG="/opt/jovyan_defaults/.claude/mcp_config.json"',
        f'CLAUDE_DEFAULT_MCP_CONFIG="{mcp_config_file}"',
    )
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    if bashrc_contents is not None:
        (home_dir / ".bashrc").write_text(bashrc_contents, encoding="utf-8")

    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("BR_MCP_TOKEN", None)
    if env_token is not None:
        env["BR_MCP_TOKEN"] = env_token

    return test_wrapper, env, mcp_config_file


def test_claude_adds_mcp_config_when_br_token_in_bashrc(tmp_path):
    """Verify Claude wrapper passes --mcp-config when BR_MCP_TOKEN is set in .bashrc."""
    test_wrapper, env, mcp_config_file = _make_claude_wrapper_with_token(
        tmp_path,
        bashrc_contents="export BR_MCP_TOKEN='from-bashrc-token'\n",
    )

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert f"ARG:--mcp-config" in result.stdout
    assert f"ARG:{mcp_config_file}" in result.stdout


def test_claude_omits_mcp_config_when_no_br_token(tmp_path):
    """Verify Claude wrapper does not pass --mcp-config without a BR_MCP_TOKEN."""
    test_wrapper, env, mcp_config_file = _make_claude_wrapper_with_token(
        tmp_path,
        bashrc_contents="",
    )

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert f"ARG:{mcp_config_file}" not in result.stdout


def _make_codex_wrapper(tmp_path, bashrc_contents="", preexisting_toml=None):
    wrapper_path = Path("/usr/local/sbin/codex")
    if not wrapper_path.exists():
        pytest.skip("Codex wrapper not installed in this environment")

    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/bin/sh\necho \"FAKE_CODEX:$*\"\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    test_wrapper = tmp_path / "codex-wrapper-test"
    wrapper_contents = wrapper_path.read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace("/usr/bin/codex", str(fake_codex))
    # Neutralize the default-config copy since /opt/jovyan_defaults may or may
    # not exist in the test environment.
    wrapper_contents = wrapper_contents.replace(
        'CODEX_DEFAULT_CONFIG_TOML="/opt/jovyan_defaults/.codex/config.toml"',
        f'CODEX_DEFAULT_CONFIG_TOML="{tmp_path / "missing-default.toml"}"',
    )
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    (tmp_path / "AGENTS.md").write_text("test", encoding="utf-8")

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    if bashrc_contents:
        (home_dir / ".bashrc").write_text(bashrc_contents, encoding="utf-8")

    if preexisting_toml is not None:
        (home_dir / ".codex").mkdir(parents=True, exist_ok=True)
        (home_dir / ".codex" / "config.toml").write_text(
            preexisting_toml, encoding="utf-8"
        )

    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("BR_MCP_TOKEN", None)

    return test_wrapper, home_dir, env


def test_codex_adds_brain_researcher_mcp_with_token(tmp_path):
    """Verify Codex wrapper writes a [mcp_servers.brain-researcher] block into ~/.codex/config.toml when BR_MCP_TOKEN is set."""
    test_wrapper, home_dir, env = _make_codex_wrapper(
        tmp_path,
        bashrc_contents="export BR_MCP_TOKEN='codex-token-from-bashrc'\n",
        preexisting_toml='model = "preexisting"\n',
    )

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "Brain Researcher MCP server: ACTIVE" in result.stdout

    toml_text = (home_dir / ".codex" / "config.toml").read_text(encoding="utf-8")
    # Existing config is preserved.
    assert 'model = "preexisting"' in toml_text
    # Brain researcher block present with the correct Codex schema.
    assert "[mcp_servers.brain-researcher]" in toml_text
    assert 'url = "https://brain-researcher.com/mcp"' in toml_text
    assert 'bearer_token_env_var = "BR_MCP_TOKEN"' in toml_text
    assert "enabled = true" in toml_text
    # Block is enclosed in BEGIN/END markers so it can be safely removed later.
    assert "# BEGIN brain-researcher MCP" in toml_text
    assert "# END brain-researcher MCP" in toml_text


def test_codex_removes_brain_researcher_mcp_without_token(tmp_path):
    """Verify Codex wrapper strips a stale [mcp_servers.brain-researcher] block when BR_MCP_TOKEN is unset."""
    preexisting_toml = (
        'model = "preexisting"\n'
        "\n"
        "# BEGIN brain-researcher MCP\n"
        "[mcp_servers.brain-researcher]\n"
        'url = "https://brain-researcher.com/mcp"\n'
        'bearer_token_env_var = "BR_MCP_TOKEN"\n'
        "enabled = true\n"
        "# END brain-researcher MCP\n"
    )
    test_wrapper, home_dir, env = _make_codex_wrapper(
        tmp_path, preexisting_toml=preexisting_toml
    )

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "Brain Researcher MCP server: inactive" in result.stdout

    toml_text = (home_dir / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'model = "preexisting"' in toml_text
    assert "[mcp_servers.brain-researcher]" not in toml_text
    assert "brain-researcher" not in toml_text


def test_claude_prints_brain_researcher_banner(tmp_path):
    """Verify the claude wrapper prints a banner when BR_MCP_TOKEN is active."""
    test_wrapper, env, mcp_config_file = _make_claude_wrapper_with_token(
        tmp_path,
        bashrc_contents="export BR_MCP_TOKEN='claude-banner-token'\n",
    )

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "Brain Researcher MCP server: ACTIVE" in result.stdout


def test_claude_prints_brain_researcher_inactive_banner(tmp_path):
    """Verify the claude wrapper prints an inactive banner when no token is set."""
    test_wrapper, env, mcp_config_file = _make_claude_wrapper_with_token(
        tmp_path,
        bashrc_contents="",
    )

    result = subprocess.run(
        [str(test_wrapper), "--version"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"Wrapper execution failed: {result.stdout}"
    assert "Brain Researcher MCP server: inactive" in result.stdout
