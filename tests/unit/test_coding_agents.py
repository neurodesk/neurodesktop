import errno
import json
import os
import subprocess
import time
from pathlib import Path
import pytest

from testlib import resolve_source

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()

def opencode_wrapper_path():
    """Return the OpenCode wrapper under test, honouring an explicit override."""
    override = os.environ.get("NEURODESKTOP_TEST_OPENCODE_WRAPPER")
    if override:
        return Path(override)
    return resolve_source("/usr/local/sbin/opencode", "config/agents/opencode")


def codex_wrapper_path():
    return resolve_source("/usr/local/sbin/codex", "config/agents/codex")


def claude_wrapper_path():
    return resolve_source("/usr/local/sbin/claude", "config/agents/claude")


def test_agent_slurm_template_uses_the_submission_directory():
    guidance = resolve_source("/opt/AGENTS.md", "config/agents/AGENTS.md").read_text(
        encoding="utf-8"
    )

    assert 'PROJECT_DIR="${SLURM_SUBMIT_DIR:-$PWD}"' in guidance
    assert "Slurm executes a spool copy" in guidance


def agent_guidance():
    """The workspace contract installed as /opt/AGENTS.md."""
    return resolve_source("/opt/AGENTS.md", "config/agents/AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_agent_guidance_has_a_non_blocking_discovery_and_decision_fast_path():
    guidance = agent_guidance()
    compact = " ".join(guidance.split())

    assert "blocking question" in compact
    assert "A universe is not a job" in compact
    assert "metadata-only DataLad clones" in compact
    assert "analysis_00_download_data.sh" in guidance
    assert "alternative spellings and acronyms" in compact
    assert "git rev-parse --is-inside-work-tree" in guidance
    assert "Always ask the user which tool" not in guidance


def test_agent_guidance_prevents_stale_outputs_and_false_job_success():
    guidance = agent_guidance()
    compact = " ".join(guidance.split())

    assert "atomically rename" in compact
    assert "`test -s`" in compact
    assert "Queue disappearance is not success" in compact
    assert "`sacct`" in compact
    assert "`ExitCode` `0:0`" in compact
    # `--wait` waits out queue time too, so the recommended form must be bounded
    # and must say the job survives the timeout.
    assert "timeout 300 sbatch --parsable --wait" in compact
    assert "recovered through the ID `--parsable` already printed" in compact


def test_agent_guidance_separates_astra_validation_execution_and_provenance():
    guidance = agent_guidance()
    compact = " ".join(guidance.split())

    assert "does not execute or verify its recipe commands" in compact
    assert "save hook" in compact
    assert "**Specification:**" in guidance
    assert "**Execution:**" in guidance
    assert "**Provenance:**" in guidance
    assert "`spec-only`" in compact


def test_agent_guidance_keeps_the_environment_and_schema_lookup_facts():
    """Facts an agent cannot rediscover from the worked example alone."""
    guidance = agent_guidance()
    compact = " ".join(guidance.split())

    # `findings:` entries are Insight objects; `astra spec finding` returns
    # nothing, and this is the only place the repo records that.
    assert "astra spec insight" in guidance
    assert "no `Finding` class" in compact

    assert "await module.load(" in guidance
    assert "module help <name>" in guidance
    assert "mamba" in guidance and "uv" in guidance

    # A download step that is only a script leaves the input unexplained.
    assert "invisible in the graph" in compact
    assert "declare it as an output with its own recipe" in compact


@pytest.mark.parametrize("args", [["--version"], ["acp"]])
def test_opencode_machine_commands_bypass_interactive_setup(tmp_path, args):
    """ACP discovery and stdio transport must reach the real binary directly."""
    fake_opencode = tmp_path / "fake-opencode"
    fake_opencode.write_text(
        "#!/bin/sh\nprintf 'REAL_ARG:%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)

    test_wrapper = tmp_path / "opencode-wrapper-test"
    wrapper_contents = opencode_wrapper_path().read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace("/usr/bin/opencode", str(fake_opencode))
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    result = subprocess.run(
        [str(test_wrapper), *args],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home_dir)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == f"REAL_ARG:{args[0]}"
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (home_dir / ".config/opencode/opencode.json").exists()


def test_opencode_acp_exports_lmod_to_child_bash_shells(tmp_path):
    """ACP tool shells inherit Lmod without sourcing an interactive bashrc."""
    bash_env = tmp_path / "opencode_bash_env.sh"
    bash_env.write_text(
        "module() {\n"
        "  if [ \"$1\" = load ] && [ \"$2\" = funny-name-tool ]; then\n"
        "    return 1\n"
        "  fi\n"
        "  printf 'MODULE:%s\\n' \"$*\"\n"
        "}\n",
        encoding="utf-8",
    )

    fake_opencode = tmp_path / "fake-opencode"
    fake_opencode.write_text(
        "#!/bin/bash\n"
        "output_file=\"${TMPDIR:-/tmp}/funny-name-tool.out\"\n"
        "/bin/bash -c '\n"
        "type module >/dev/null || exit 1\n"
        "module spider fsl\n"
        "if module load funny-name-tool >\"$1\" 2>/dev/null; then exit 1; fi\n"
        "test ! -s \"$1\"\n"
        "' _ \"$output_file\"\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)

    test_wrapper = tmp_path / "opencode-wrapper-test"
    wrapper_contents = opencode_wrapper_path().read_text(encoding="utf-8")
    wrapper_contents = wrapper_contents.replace("/usr/bin/opencode", str(fake_opencode))
    wrapper_contents = wrapper_contents.replace(
        "/opt/neurodesktop/opencode_bash_env.sh", str(bash_env)
    )
    test_wrapper.write_text(wrapper_contents, encoding="utf-8")
    test_wrapper.chmod(0o755)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    result = subprocess.run(
        [str(test_wrapper), "acp"],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(home_dir),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "MODULE:spider fsl"


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
                if [ -n "${FAKE_NEURODESK_MODELS_JSON:-}" ]; then
                    printf '%s' "$FAKE_NEURODESK_MODELS_JSON" > "$outfile"
                else
                    printf '%s' '{"data":[{"id":"model-alpha"},{"id":"openai/gpt-4.1-mini"}]}' > "$outfile"
                fi
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

def test_opencode_drops_the_legacy_global_agents_md_instruction(tmp_path):
    """An existing config keeps only the editable per-project AGENTS.md.

    Earlier releases wrote "instructions": ["/opt/AGENTS.md"] into
    ~/.config/opencode/opencode.json. That read-only copy shadowed the
    AGENTS.md the wrapper seeds into the working directory, so a user editing
    it saw no effect. The wrapper must strip that entry from configs already
    on disk while preserving instructions the user added themselves.
    """
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    config_path = home_dir / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "model": "neurodesk/gpt-oss",
            "instructions": ["/opt/AGENTS.md", "./docs/my-rules.md"],
            "provider": {},
        }),
        encoding="utf-8",
    )
    env["NEURODESK_API_KEY"] = "neurodesk-test-key"
    env["OPENCODE_MODEL_PROFILE"] = "neurodesk"

    returncode, output = run_pty_command(
        [str(test_wrapper)], "n\n", cwd=tmp_path, env=env
    )

    assert returncode == 0, output
    user_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert user_config["instructions"] == ["./docs/my-rules.md"]

def test_opencode_removes_an_instructions_key_left_empty(tmp_path):
    """Stripping the only entry drops the key instead of leaving an empty list."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    config_path = home_dir / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "model": "neurodesk/gpt-oss",
            "instructions": ["/opt/AGENTS.md"],
            "provider": {},
        }),
        encoding="utf-8",
    )
    env["NEURODESK_API_KEY"] = "neurodesk-test-key"
    env["OPENCODE_MODEL_PROFILE"] = "neurodesk"

    returncode, output = run_pty_command(
        [str(test_wrapper)], "n\n", cwd=tmp_path, env=env
    )

    assert returncode == 0, output
    user_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "instructions" not in user_config

def test_opencode_neurodesk_profile_prefers_the_curated_alias_model(tmp_path):
    """OPENCODE_MODEL_PROFILE=neurodesk must pick the "neurodesk" alias model.

    llm.neurodesk.org publishes a curated default model literally named
    "neurodesk", but its /models listing returns it last; the profile must
    not settle for whichever model the server happens to list first.
    """
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["NEURODESK_API_KEY"] = "neurodesk-test-key"
    env["OPENCODE_MODEL_PROFILE"] = "neurodesk"
    env["FAKE_NEURODESK_MODELS_JSON"] = (
        '{"data":[{"id":"qwen3"},{"id":"neurodesk"}]}'
    )

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "n\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert (
        "OPENCODE_MODEL_PROFILE=neurodesk requested; "
        "using neurodesk/neurodesk." in output
    )
    assert "OpenCode default model set to neurodesk/neurodesk." in output

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    assert user_config["model"] == "neurodesk/neurodesk"

def test_opencode_neurodesk_profile_falls_back_to_first_listed_model(tmp_path):
    """Without the curated alias, the profile keeps the first listed model."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["NEURODESK_API_KEY"] = "neurodesk-test-key"
    env["OPENCODE_MODEL_PROFILE"] = "neurodesk"

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "n\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert (
        "OPENCODE_MODEL_PROFILE=neurodesk requested; "
        "using neurodesk/model-alpha." in output
    )

    user_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    assert user_config["model"] == "neurodesk/model-alpha"

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

def test_opencode_wrapper_syncs_notebook_intelligence(tmp_path):
    """Verify the wrapper pushes the model/key selection to Notebook Intelligence."""
    test_wrapper, _home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    marker = tmp_path / "nbi-sync-marker"
    fake_nbi_setup = tmp_path / "fake-nbi-setup"
    fake_nbi_setup.write_text(
        "#!/bin/sh\n" f"printf '%s' \"${{NEURODESK_API_KEY:-}}\" > '{marker}'\n",
        encoding="utf-8",
    )
    fake_nbi_setup.chmod(0o755)
    env["NBI_SETUP_SCRIPT"] = str(fake_nbi_setup)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "OpenCode default model set to neurodesk/model-alpha." in output
    assert "Notebook Intelligence follows the OpenCode model selection" in output
    # nbi_setup.sh ran after key entry and saw the freshly exported key.
    assert marker.read_text(encoding="utf-8") == "neurodesk-test-key"

def test_opencode_wrapper_hides_tui_sidebar_by_default(tmp_path):
    """Verify the wrapper seeds kv.json so the TUI sidebar starts hidden."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    kv_file = home_dir / ".local" / "state" / "opencode" / "kv.json"
    assert kv_file.exists(), "wrapper did not seed the OpenCode TUI kv store"
    kv = json.loads(kv_file.read_text(encoding="utf-8"))
    assert kv["sidebar"] == "hide"

def test_opencode_wrapper_keeps_user_sidebar_choice(tmp_path):
    """Verify an existing sidebar preference in kv.json is never overwritten."""
    test_wrapper, home_dir, env = make_opencode_litellm_wrapper(tmp_path)

    kv_file = home_dir / ".local" / "state" / "opencode" / "kv.json"
    kv_file.parent.mkdir(parents=True)
    kv_file.write_text(
        json.dumps({"sidebar": "auto", "theme": "tokyonight"}), encoding="utf-8"
    )

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    kv = json.loads(kv_file.read_text(encoding="utf-8"))
    assert kv["sidebar"] == "auto"
    assert kv["theme"] == "tokyonight"

def test_opencode_wrapper_skips_nbi_sync_when_script_missing(tmp_path):
    """Verify a missing nbi_setup.sh is not an error (non-container installs)."""
    test_wrapper, _home_dir, env = make_opencode_litellm_wrapper(tmp_path)
    env["NBI_SETUP_SCRIPT"] = str(tmp_path / "does-not-exist")

    returncode, output = run_pty_command(
        [str(test_wrapper)],
        "neurodesk-test-key\n1\nn\n",
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0, output
    assert "Notebook Intelligence" not in output

def test_codex_yolo_no_full_auto(tmp_path):
    """Verify Codex wrapper does not combine --yolo with --full-auto."""
    wrapper_path = codex_wrapper_path()

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
    wrapper_path = codex_wrapper_path()

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
    wrapper_path = codex_wrapper_path()

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

@pytest.mark.parametrize("existing_kind", ["regular_file", "dangling_symlink"])
def test_claude_links_user_binary_to_image_binary(tmp_path, existing_kind):
    """The wrapper replaces stale user binaries with the image-owned binary."""
    wrapper_path = claude_wrapper_path()

    home_dir = tmp_path / "home"
    bin_dir = home_dir / ".local" / "bin"
    bin_dir.mkdir(parents=True)

    claude_link = bin_dir / "claude"
    if existing_kind == "regular_file":
        claude_link.write_text(
            "#!/bin/sh\n"
            "echo STALE_LOCAL_CLAUDE\n",
            encoding="utf-8",
        )
        claude_link.chmod(0o755)
    else:
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
    assert claude_link.is_symlink(), (
        "The user-local Claude path must be a symlink, not a persistent binary copy"
    )
    assert claude_link.resolve() == fake_default_claude.resolve()
    assert "STALE_LOCAL_CLAUDE" not in result.stdout
    assert str(claude_link) in result.stdout, "Wrapper did not execute the managed symlink"
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
    wrapper_path = claude_wrapper_path()

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
    wrapper_path = codex_wrapper_path()

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


@pytest.mark.parametrize(
    ("bashrc_contents", "expected_brain_tables"),
    [
        ("", 0),
        ("export BR_MCP_TOKEN='codex-token-from-bashrc'\n", 1),
    ],
)
def test_codex_preserves_plugin_sections_inserted_inside_managed_mcp_markers(
    tmp_path, bashrc_contents, expected_brain_tables
):
    """Codex-managed plugin tables must survive the wrapper's MCP refresh."""
    preexisting_toml = (
        'model = "preexisting"\n'
        "\n"
        "# BEGIN brain-researcher MCP\n"
        "[mcp_servers.brain-researcher]\n"
        'url = "https://brain-researcher.com/mcp"\n'
        'bearer_token_env_var = "BR_MCP_TOKEN"\n'
        "enabled = true\n"
        "\n"
        "[marketplaces.lightcone-research]\n"
        'source_type = "git"\n'
        'source = "https://github.com/LightconeResearch/agent-skills.git"\n'
        "\n"
        '[plugins."astra@lightcone-research"]\n'
        "enabled = true\n"
        "# END brain-researcher MCP\n"
    )
    test_wrapper, home_dir, env = _make_codex_wrapper(
        tmp_path,
        bashrc_contents=bashrc_contents,
        preexisting_toml=preexisting_toml,
    )

    for _ in range(2):
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

    toml_text = (home_dir / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert toml_text.count("[marketplaces.lightcone-research]") == 1
    assert (
        'source = "https://github.com/LightconeResearch/agent-skills.git"'
        in toml_text
    )
    assert toml_text.count('[plugins."astra@lightcone-research"]') == 1
    assert (
        toml_text.count("[mcp_servers.brain-researcher]")
        == expected_brain_tables
    )


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
