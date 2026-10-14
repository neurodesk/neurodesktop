"""Behavior of host_slurm_environment.sh, the host Slurm client helper.

In ``NEURODESKTOP_SLURM_MODE=host`` the container uses the cluster's Slurm
through a bound ``slurm.conf`` and auth socket. before_notebook.sh sources the
helper and calls ``configure_host_slurm_environment --warn`` in the shell that
later execs Jupyter; environment_variables.sh sources it and calls it quietly
in every shell and kernel. These tests source the real helper in a Bash
subprocess, point its search paths at a temporary root, and read the exports
from a child process, which is what Jupyter, terminals and kernels inherit.
"""

import os
import shlex
import socket
import subprocess

import pytest

from testlib import repo_path

HELPER = repo_path("config/jupyter/host_slurm_environment.sh")
BEFORE_NOTEBOOK = repo_path("config/jupyter/before_notebook.sh")
ENVIRONMENT_VARIABLES = repo_path("config/jupyter/environment_variables.sh")
INSTALLED_HELPER = "/opt/neurodesktop/host_slurm_environment.sh"

EXPORTS = ("SLURM_CONF", "MUNGE_SOCKET", "SLURM_SACK_SOCKET")
UNSET = None

MISSING_CONF_WARNING = (
    "[WARN] Host Slurm mode selected but no readable slurm.conf was found."
)
MUNGE_WARNING = (
    "[WARN] Host Slurm uses AuthType=auth/munge but no MUNGE socket was found."
)
SACK_WARNING = (
    "[WARN] Host Slurm uses AuthType=auth/slurm but no sack.socket was found."
)
NO_SOCKET_WARNING = (
    "[WARN] Host Slurm mode selected but neither MUNGE nor sack.socket was found."
)


def make_socket(path):
    """Create a Unix socket file; bind relative to dodge the sun_path limit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = os.getcwd()
    os.chdir(path.parent)
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(path.name)
    finally:
        os.chdir(previous)
    return path


def write_conf(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


def clean_env(**overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in EXPORTS and not key.startswith("NEURODESKTOP_")
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return env


REPORT = (
    'echo "STATUS=$?"\n'
    "bash -c 'for name in SLURM_CONF MUNGE_SOCKET SLURM_SACK_SOCKET; do "
    'if [ -n "${!name+x}" ]; then printf "CHILD %s=%s\\n" "$name" "${!name}"; fi; '
    "done'\n"
)


def parse(stdout):
    lines = stdout.splitlines()
    inherited = {name: UNSET for name in EXPORTS}
    for line in lines:
        if line.startswith("CHILD "):
            name, value = line.removeprefix("CHILD ").split("=", 1)
            inherited[name] = value
    messages = [line for line in lines if line.startswith("[")]
    return inherited, messages, lines


def configure(root, *, warn=True, **env):
    """Source the helper, configure host Slurm, and report what a child sees."""
    arguments = ["--warn"] if warn else []
    arguments.append(str(root))
    script = (
        f"source {shlex.quote(str(HELPER))}\n"
        f"configure_host_slurm_environment {shlex.join(arguments)}\n" + REPORT
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=clean_env(**env),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    inherited, messages, lines = parse(result.stdout)
    assert "STATUS=0" in lines, result.stdout
    return inherited, messages


@pytest.fixture
def root(tmp_path):
    # A space in the root proves that every search path is quoted.
    path = tmp_path / "host root"
    path.mkdir()
    return path


# ---------------------------------------------------------------------------
# Packaging contract: installed at a stable path and wired into both callers
# ---------------------------------------------------------------------------


def test_helper_is_installed_at_image_owned_path():
    dockerfile = repo_path("Dockerfile").read_text()
    assert (
        "install -m 0644 /tmp/jupyter/host_slurm_environment.sh "
        f"{INSTALLED_HELPER}"
    ) in dockerfile


def test_startup_sources_helper_with_warnings_in_host_mode_only():
    startup = BEFORE_NOTEBOOK.read_text()
    call = f"source {INSTALLED_HELPER}\n    configure_host_slurm_environment --warn\n"
    assert startup.count(call) == 1
    position = startup.index(call)
    # Same slot as the inline call it replaced: inside the host branch, after
    # mode selection and before environment_variables.sh and the deferred worker.
    host_branch = startup.index('if [ "${NEURODESKTOP_SLURM_MODE}" = "host" ]; then')
    assert startup.index("export NEURODESKTOP_SLURM_MODE=") < host_branch < position
    assert position < startup.index("export NEURODESKTOP_SLURM_ENABLE=0")
    assert position < startup.index("source /opt/neurodesktop/environment_variables.sh")
    assert position < startup.index("deferred_startup.sh &")
    # The logic lives in one place only.
    assert "resolve_sack_socket" not in startup
    assert "munge.socket.2" not in startup


def test_shell_environment_sources_helper_quietly_in_host_mode_only():
    environment = ENVIRONMENT_VARIABLES.read_text()
    call = f"source {INSTALLED_HELPER}\n                configure_host_slurm_environment\n"
    assert environment.count(call) == 1
    position = environment.index(call)
    host_case = environment.index('case "${NEURODESKTOP_SLURM_MODE}" in')
    assert host_case < environment.index("host)", host_case) < position
    assert position < environment.index("*)", host_case)
    assert "resolve_sack_socket" not in environment
    assert "munge.socket.2" not in environment


def run_environment_variables(tmp_path, **env):
    """Source a copy of environment_variables.sh that loads the checkout helper."""
    script = tmp_path / "environment_variables.sh"
    script.write_text(
        ENVIRONMENT_VARIABLES.read_text().replace(INSTALLED_HELPER, str(HELPER))
    )
    defaults = {
        "CVMFS_DISABLE": "true",
        "HOME": tmp_path,
        "NEURODESKTOP_ENV_SOURCED": "1",
        "USER": "test-user",
        "NEURODESKTOP_LOCAL_CONTAINERS": tmp_path / "containers",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(script))} >/dev/null 2>&1\n"
            "declare -F configure_host_slurm_environment >/dev/null "
            "&& echo HELPER_LOADED\n" + REPORT,
        ],
        capture_output=True,
        text=True,
        env=clean_env(**{**defaults, **env}),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    inherited, _, lines = parse(result.stdout)
    return inherited, "HELPER_LOADED" in lines


def test_shell_environment_exports_host_configuration(tmp_path):
    conf = write_conf(tmp_path / "cluster.conf", "ClusterName=hpc", "AuthType=auth/munge")
    munge = make_socket(tmp_path / "munge.sock")
    inherited, loaded = run_environment_variables(
        tmp_path,
        NEURODESKTOP_SLURM_MODE="HOST",
        SLURM_CONF=conf,
        MUNGE_SOCKET=munge,
        SLURM_SACK_SOCKET=tmp_path / "stale.sock",
    )
    assert loaded
    assert inherited["SLURM_CONF"] == str(conf)
    assert inherited["MUNGE_SOCKET"] == str(munge)


def test_shell_environment_local_mode_does_not_load_helper(tmp_path):
    inherited, loaded = run_environment_variables(
        tmp_path, NEURODESKTOP_SLURM_MODE="local", SLURM_CONF=tmp_path / "host.conf"
    )
    assert not loaded
    assert inherited["SLURM_CONF"] == "/etc/slurm/slurm.conf"


def test_sourcing_only_defines_functions():
    script = (
        f"source {shlex.quote(str(HELPER))}\n"
        "declare -F configure_host_slurm_environment >/dev/null || exit 3\n"
        "env | grep -E '^(SLURM_CONF|MUNGE_SOCKET|SLURM_SACK_SOCKET)=' && exit 4\n"
        "exit 0\n"
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=clean_env()
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# slurm.conf resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "etc/slurm/slurm.conf",
        "etc/slurm-llnl/slurm.conf",
        "run/host/etc/slurm/slurm.conf",
        "host/etc/slurm/slurm.conf",
    ],
)
def test_bound_host_config_is_found(root, location):
    conf = write_conf(root / location, "ClusterName=hpc", "AuthType=auth/munge")
    munge = make_socket(root / "run/munge/munge.socket.2")
    inherited, messages = configure(root)
    assert inherited["SLURM_CONF"] == str(conf)
    assert inherited["MUNGE_SOCKET"] == str(munge)
    assert messages == []


def test_search_order_prefers_etc_slurm(root):
    write_conf(root / "host/etc/slurm/slurm.conf", "ClusterName=last")
    write_conf(root / "etc/slurm-llnl/slurm.conf", "ClusterName=second")
    first = write_conf(root / "etc/slurm/slurm.conf", "ClusterName=first")
    inherited, _ = configure(root)
    assert inherited["SLURM_CONF"] == str(first)


def test_local_neurodesktop_config_is_skipped(root):
    write_conf(root / "etc/slurm/slurm.conf", "ClusterName=neurodesktop")
    host = write_conf(root / "etc/slurm-llnl/slurm.conf", "ClusterName=hpc")
    inherited, _ = configure(root)
    assert inherited["SLURM_CONF"] == str(host)


def test_readable_host_slurm_conf_wins(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "ClusterName=bound")
    chosen = write_conf(tmp_path / "site conf/slurm.conf", "ClusterName=site")
    inherited, _ = configure(root, SLURM_CONF=chosen)
    assert inherited["SLURM_CONF"] == str(chosen)


@pytest.mark.parametrize("cluster_line", ["ClusterName=neurodesktop", "ClusterName=neurodesktop # local"])
def test_local_slurm_conf_value_falls_back_to_search(root, tmp_path, cluster_line):
    local = write_conf(tmp_path / "local.conf", cluster_line)
    host = write_conf(root / "etc/slurm/slurm.conf", "ClusterName=hpc")
    inherited, _ = configure(root, SLURM_CONF=local)
    assert inherited["SLURM_CONF"] == str(host)


def test_similar_cluster_name_is_not_local(root, tmp_path):
    conf = write_conf(tmp_path / "hpc.conf", "ClusterName=neurodesktop2")
    inherited, _ = configure(root, SLURM_CONF=conf)
    assert inherited["SLURM_CONF"] == str(conf)


def test_missing_config_warns_and_clears_local_slurm_conf(root, tmp_path):
    local = write_conf(tmp_path / "local.conf", "ClusterName=neurodesktop")
    inherited, messages = configure(root, SLURM_CONF=local)
    assert inherited["SLURM_CONF"] is UNSET
    assert messages == [
        MISSING_CONF_WARNING,
        "[WARN] Bind your host Slurm config (for example: --bind /etc/slurm:/etc/slurm).",
        NO_SOCKET_WARNING,
        "[WARN] Bind your host auth socket paths (for example /run/munge or /run/slurm).",
    ]


def test_missing_config_keeps_unreadable_user_slurm_conf(root, tmp_path):
    # An unbound path could become readable later; only the local
    # neurodesktop config is removed.
    missing = tmp_path / "not-bound/slurm.conf"
    inherited, messages = configure(root, SLURM_CONF=missing)
    assert inherited["SLURM_CONF"] == str(missing)
    assert MISSING_CONF_WARNING in messages


def test_missing_config_without_slurm_conf_leaves_it_unset(root):
    inherited, messages = configure(root)
    assert inherited == {name: UNSET for name in EXPORTS}
    assert MISSING_CONF_WARNING in messages


# ---------------------------------------------------------------------------
# Auth sockets
# ---------------------------------------------------------------------------


def test_explicit_munge_socket_wins(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "AuthType=auth/munge")
    make_socket(root / "run/munge/munge.socket.2")
    explicit = make_socket(tmp_path / "munge.sock")
    inherited, messages = configure(root, MUNGE_SOCKET=explicit)
    assert inherited["MUNGE_SOCKET"] == str(explicit)
    assert messages == []


def test_non_socket_munge_value_falls_back_to_var_run(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "AuthType=auth/munge")
    regular = tmp_path / "not-a-socket"
    regular.write_text("")
    fallback = make_socket(root / "var/run/munge/munge.socket.2")
    inherited, _ = configure(root, MUNGE_SOCKET=regular)
    assert inherited["MUNGE_SOCKET"] == str(fallback)


def test_missing_munge_socket_warns_and_keeps_value(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "AuthType = AUTH/MUNGE # site")
    stale = tmp_path / "gone.sock"
    inherited, messages = configure(root, MUNGE_SOCKET=stale)
    assert inherited["MUNGE_SOCKET"] == str(stale)
    assert messages == [
        MUNGE_WARNING,
        "[WARN] Bind /run/munge or /var/run/munge into the container.",
    ]


def test_cluster_specific_sack_socket_wins(root):
    write_conf(root / "etc/slurm/slurm.conf", "ClusterName=hpc", "AuthType=auth/slurm")
    make_socket(root / "run/slurm/sack.socket")
    cluster = make_socket(root / "run/slurm-hpc/sack.socket")
    inherited, messages = configure(root)
    assert inherited["SLURM_SACK_SOCKET"] == str(cluster)
    assert messages == []


@pytest.mark.parametrize(
    "location",
    [
        "run/slurm/sack.socket",
        "run/slurmctld/sack.socket",
        "run/slurmdbd/sack.socket",
        "var/run/slurm/sack.socket",
        "var/run/slurmctld/sack.socket",
        "var/run/slurmdbd/sack.socket",
    ],
)
def test_runtime_sack_sockets_are_found(root, location):
    write_conf(root / "etc/slurm/slurm.conf", "  AuthType = auth/slurm  # sackd")
    sack = make_socket(root / location)
    inherited, messages = configure(root)
    assert inherited["SLURM_SACK_SOCKET"] == str(sack)
    assert messages == []


def test_explicit_sack_socket_wins(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "ClusterName=hpc", "AuthType=auth/slurm")
    make_socket(root / "run/slurm-hpc/sack.socket")
    explicit = make_socket(tmp_path / "sack.sock")
    inherited, _ = configure(root, SLURM_SACK_SOCKET=explicit)
    assert inherited["SLURM_SACK_SOCKET"] == str(explicit)


def test_auth_slurm_without_socket_warns_and_clears_stale_value(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "AuthType=auth/slurm")
    inherited, messages = configure(root, SLURM_SACK_SOCKET=tmp_path / "stale.sock")
    assert inherited["SLURM_SACK_SOCKET"] is UNSET
    assert messages == [
        SACK_WARNING,
        "[WARN] Bind the host Slurm runtime directory (for example: --bind /run/slurm:/run/slurm).",
    ]


def test_other_auth_keeps_stale_sack_value(root, tmp_path):
    write_conf(root / "etc/slurm/slurm.conf", "AuthType=auth/munge")
    munge = make_socket(root / "run/munge/munge.socket.2")
    stale = tmp_path / "stale.sock"
    inherited, _ = configure(root, SLURM_SACK_SOCKET=stale)
    assert inherited["MUNGE_SOCKET"] == str(munge)
    assert inherited["SLURM_SACK_SOCKET"] == str(stale)


@pytest.mark.parametrize("auth_line", [None, "AuthType=auth/jwt"])
def test_unknown_auth_accepts_either_socket(root, auth_line):
    write_conf(root / "etc/slurm/slurm.conf", *([auth_line] if auth_line else []))
    sack = make_socket(root / "run/slurm/sack.socket")
    inherited, messages = configure(root)
    assert inherited["SLURM_SACK_SOCKET"] == str(sack)
    assert inherited["MUNGE_SOCKET"] is UNSET
    assert messages == []


@pytest.mark.parametrize("auth_line", [None, "AuthType=auth/jwt"])
def test_unknown_auth_without_sockets_warns(root, auth_line):
    write_conf(root / "etc/slurm/slurm.conf", *([auth_line] if auth_line else []))
    _, messages = configure(root)
    assert NO_SOCKET_WARNING in messages


def test_commented_auth_type_is_ignored(root):
    write_conf(root / "etc/slurm/slurm.conf", "# AuthType=auth/slurm", "  # AuthType=auth/munge")
    _, messages = configure(root)
    assert SACK_WARNING not in messages
    assert MUNGE_WARNING not in messages
    assert NO_SOCKET_WARNING in messages


# ---------------------------------------------------------------------------
# Quiet mode used by every shell
# ---------------------------------------------------------------------------


def test_quiet_mode_exports_the_same_values_without_output(root, tmp_path):
    write_conf(tmp_path / "local.conf", "ClusterName=neurodesktop")
    write_conf(root / "etc/slurm/slurm.conf", "ClusterName=hpc", "AuthType=auth/slurm")
    sack = make_socket(root / "run/slurm-hpc/sack.socket")
    loud, loud_messages = configure(root, SLURM_CONF=tmp_path / "local.conf")
    quiet, quiet_messages = configure(root, warn=False, SLURM_CONF=tmp_path / "local.conf")
    assert quiet == loud
    assert quiet["SLURM_SACK_SOCKET"] == str(sack)
    assert loud_messages == quiet_messages == []


def test_quiet_mode_is_silent_when_nothing_is_bound(root, tmp_path):
    local = write_conf(tmp_path / "local.conf", "ClusterName=neurodesktop")
    inherited, messages = configure(
        root, warn=False, SLURM_CONF=local, SLURM_SACK_SOCKET=tmp_path / "stale"
    )
    assert inherited["SLURM_CONF"] is UNSET
    # Without a config the auth type is unknown, so the stale value stays.
    assert inherited["SLURM_SACK_SOCKET"] == str(tmp_path / "stale")
    assert messages == []


def test_repeated_calls_converge(root, tmp_path):
    """The startup shell runs both callers; the second call changes nothing."""
    write_conf(root / "etc/slurm/slurm.conf", "AuthType=auth/slurm")
    script = (
        f"source {shlex.quote(str(HELPER))}\n"
        "state() { local v; for v in SLURM_CONF MUNGE_SOCKET SLURM_SACK_SOCKET; do "
        'printf "%s=%s\\n" "$v" "${!v-<unset>}"; done; }\n'
        f"configure_host_slurm_environment --warn {shlex.quote(str(root))}\n"
        "first=$(state)\n"
        f"configure_host_slurm_environment {shlex.quote(str(root))}\n"
        "second=$(state)\n"
        '[ "$first" = "$second" ] || { printf "%s\\n---\\n%s\\n" "$first" "$second"; exit 5; }\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=clean_env(SLURM_SACK_SOCKET=tmp_path / "stale"),
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_root_searches_absolute_paths(tmp_path):
    """Without a root argument the helper looks at the real system paths."""
    script = (
        f"source {shlex.quote(str(HELPER))}\n"
        "set -x\n"
        "resolve_munge_socket >/dev/null || true\n"
        "resolve_host_slurm_conf >/dev/null || true\n"
        "resolve_sack_socket hpc >/dev/null || true\n"
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=clean_env()
    )
    trace = result.stderr
    for path in (
        "/run/munge/munge.socket.2",
        "/var/run/munge/munge.socket.2",
        "/etc/slurm/slurm.conf",
        "/host/etc/slurm/slurm.conf",
        "/run/slurm-hpc/sack.socket",
        "/var/run/slurmdbd/sack.socket",
    ):
        assert f" {path}" in trace, path
    assert "//" not in trace
