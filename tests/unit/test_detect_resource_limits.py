"""Behavior of detect_resource_limits.sh, the startup resource-limit helper.

before_notebook.sh sources the helper and calls ``detect_resource_limits`` so
that MEM_LIMIT and CPU_LIMIT reach the Jupyter server for
jupyter-resource-usage. These tests source the real helper in a Bash
subprocess, point it at a temporary cgroup tree, and read the exported values
from a child process, which is what the Jupyter server inherits.
"""

import os
import shlex
import subprocess

import pytest

from testlib import repo_path

HELPER = repo_path("config/jupyter/detect_resource_limits.sh")
BEFORE_NOTEBOOK = repo_path("config/jupyter/before_notebook.sh")
INSTALLED_HELPER = "/opt/neurodesktop/detect_resource_limits.sh"

GIB = 1024**3
MIB = 1024**2


def write_tree(root, files):
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents + "\n")
    return root


def clean_env(**overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SLURM_") and key not in {"MEM_LIMIT", "CPU_LIMIT"}
    }
    env.update(overrides)
    return env


def detect(cgroup_root, **env):
    """Source the helper, run detection, and report what a child inherits."""
    script = (
        f"source {shlex.quote(str(HELPER))}\n"
        f"detect_resource_limits {shlex.quote(str(cgroup_root))}\n"
        'echo "STATUS=$?"\n'
        "env | grep -E '^(MEM|CPU)_LIMIT=' | sed 's/^/CHILD_/'\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=clean_env(**env),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "STATUS=0" in lines, result.stdout
    inherited = dict(
        line.removeprefix("CHILD_").split("=", 1)
        for line in lines
        if line.startswith("CHILD_")
    )
    return inherited, result.stdout


# ---------------------------------------------------------------------------
# Packaging contract: installed at a stable path and wired into startup
# ---------------------------------------------------------------------------


def test_helper_is_installed_at_image_owned_path():
    dockerfile = repo_path("Dockerfile").read_text()
    assert (
        "install -m 0644 /tmp/jupyter/detect_resource_limits.sh "
        f"{INSTALLED_HELPER}"
    ) in dockerfile


def test_startup_sources_helper_then_detects_before_slurm_mode():
    startup = BEFORE_NOTEBOOK.read_text()
    source_line = f"source {INSTALLED_HELPER}\ndetect_resource_limits\n"
    assert source_line in startup
    position = startup.index(source_line)
    # Same slot as the inline detection it replaced: after the .bashrc
    # setup and before Slurm mode selection and the deferred worker.
    assert startup.index("# Neurodesk persistent bash history") < position
    assert position < startup.index("# Slurm mode:")
    assert position < startup.index("deferred_startup.sh &")
    # The logic lives in one place only.
    assert "memory.max" not in startup
    assert "cpu.cfs_quota_us" not in startup


def test_sourcing_only_defines_functions(tmp_path):
    script = (
        f"source {shlex.quote(str(HELPER))}\n"
        "declare -F detect_resource_limits >/dev/null || exit 3\n"
        "env | grep -E '^(MEM|CPU)_LIMIT=' && exit 4\n"
        "exit 0\n"
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=clean_env()
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# cgroup v2
# ---------------------------------------------------------------------------


def test_cgroup_v2_limits_are_exported(tmp_path):
    root = write_tree(
        tmp_path, {"memory.max": str(2 * GIB), "cpu.max": "150000 100000"}
    )
    inherited, output = detect(root)
    assert inherited == {"MEM_LIMIT": str(2 * GIB), "CPU_LIMIT": "1.50"}
    assert "Memory limit detected (cgroup v2): 2.0G" in output
    assert "CPU limit detected (cgroup v2): 1.50 CPUs" in output


def test_cgroup_v2_unlimited_keeps_existing_values(tmp_path):
    root = write_tree(tmp_path, {"memory.max": "max", "cpu.max": "max 100000"})
    inherited, output = detect(root, MEM_LIMIT="12345", CPU_LIMIT="7")
    assert inherited == {"MEM_LIMIT": "12345", "CPU_LIMIT": "7"}
    assert output.count("limit: unlimited") == 2


@pytest.mark.parametrize("cpu_max", ["abc 100000", "150000", "150000 0", ""])
def test_cgroup_v2_malformed_cpu_max_sets_no_cpu_limit(tmp_path, cpu_max):
    root = write_tree(tmp_path, {"memory.max": "max", "cpu.max": cpu_max})
    inherited, output = detect(root)
    assert "CPU_LIMIT" not in inherited
    assert "CPU limit detected" not in output


def test_cgroup_v2_takes_precedence_over_v1_files(tmp_path):
    root = write_tree(
        tmp_path,
        {
            "memory.max": str(GIB),
            "memory/memory.limit_in_bytes": str(4 * GIB),
            "cpu.max": "100000 100000",
            "cpu/cpu.cfs_quota_us": "300000",
            "cpu/cpu.cfs_period_us": "100000",
        },
    )
    inherited, _ = detect(root)
    assert inherited == {"MEM_LIMIT": str(GIB), "CPU_LIMIT": "1.00"}


# ---------------------------------------------------------------------------
# cgroup v1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "memory_file,cpu_dir",
    [
        ("memory/memory.limit_in_bytes", "cpu"),
        ("memory.limit_in_bytes", "cpu,cpuacct"),
        ("memory.limit_in_bytes", ""),
    ],
)
def test_cgroup_v1_limits_are_exported(tmp_path, memory_file, cpu_dir):
    prefix = f"{cpu_dir}/" if cpu_dir else ""
    root = write_tree(
        tmp_path,
        {
            memory_file: str(GIB),
            f"{prefix}cpu.cfs_quota_us": "50000",
            f"{prefix}cpu.cfs_period_us": "100000",
        },
    )
    inherited, output = detect(root)
    assert inherited == {"MEM_LIMIT": str(GIB), "CPU_LIMIT": "0.50"}
    assert "Memory limit detected (cgroup v1): 1.0G" in output
    assert "CPU limit detected (cgroup v1): 0.50 CPUs" in output


def test_cgroup_v1_subdirectory_memory_file_wins(tmp_path):
    root = write_tree(
        tmp_path,
        {"memory/memory.limit_in_bytes": str(GIB), "memory.limit_in_bytes": "4096"},
    )
    inherited, _ = detect(root)
    assert inherited["MEM_LIMIT"] == str(GIB)


@pytest.mark.parametrize(
    "memory_limit", ["9223372036854771712", "0", "junk", "-1", ""]
)
def test_cgroup_v1_unlimited_or_malformed_memory_is_not_exported(
    tmp_path, memory_limit
):
    root = write_tree(tmp_path, {"memory/memory.limit_in_bytes": memory_limit})
    inherited, output = detect(root)
    assert "MEM_LIMIT" not in inherited
    assert "Memory limit: unlimited" in output


@pytest.mark.parametrize(
    "files",
    [
        {"cpu/cpu.cfs_quota_us": "-1", "cpu/cpu.cfs_period_us": "100000"},
        {"cpu/cpu.cfs_quota_us": "50000"},  # period file missing
        {"cpu/cpu.cfs_quota_us": "junk", "cpu/cpu.cfs_period_us": "100000"},
        {"cpu/cpu.cfs_quota_us": "50000", "cpu/cpu.cfs_period_us": "junk"},
    ],
)
def test_cgroup_v1_unlimited_or_malformed_cpu_is_not_exported(tmp_path, files):
    root = write_tree(tmp_path, files)
    inherited, output = detect(root)
    assert "CPU_LIMIT" not in inherited
    assert "CPU limit: unlimited" in output


def test_missing_cgroup_files_are_reported_without_failing(tmp_path):
    inherited, output = detect(tmp_path / "absent", MEM_LIMIT="1", CPU_LIMIT="2")
    assert inherited == {"MEM_LIMIT": "1", "CPU_LIMIT": "2"}
    assert "No cgroup memory limit file found." in output
    assert "No cgroup cpu limit file found." in output


def test_cgroup_root_with_spaces_is_quoted(tmp_path):
    root = write_tree(
        tmp_path / "cgroup root",
        {
            "memory/memory.limit_in_bytes": str(GIB),
            "cpu,cpuacct/cpu.cfs_quota_us": "200000",
            "cpu,cpuacct/cpu.cfs_period_us": "100000",
        },
    )
    inherited, _ = detect(root)
    assert inherited == {"MEM_LIMIT": str(GIB), "CPU_LIMIT": "2.00"}


# ---------------------------------------------------------------------------
# Slurm allocations override cgroup values inside a job
# ---------------------------------------------------------------------------


@pytest.fixture
def cgroup_limited(tmp_path):
    return write_tree(
        tmp_path, {"memory.max": str(8 * GIB), "cpu.max": "400000 100000"}
    )


def test_slurm_values_are_ignored_outside_a_job(cgroup_limited):
    inherited, output = detect(
        cgroup_limited, SLURM_MEM_PER_NODE="2G", SLURM_CPUS_ON_NODE="3"
    )
    assert inherited == {"MEM_LIMIT": str(8 * GIB), "CPU_LIMIT": "4.00"}
    assert "SLURM" not in output


@pytest.mark.parametrize(
    "slurm_env,mem_limit,cpu_limit",
    [
        ({"SLURM_MEM_PER_NODE": "2G", "SLURM_CPUS_ON_NODE": "3"}, 2 * GIB, "3"),
        ({"SLURM_MEM_PER_NODE": "1536"}, 1536 * MIB, "4.00"),
        ({"SLURM_MEM_PER_NODE": "1T"}, 1024 * 1024 * MIB, "4.00"),
        # K rounds up to whole MiB.
        ({"SLURM_MEM_PER_NODE": "1025K"}, 2 * MIB, "4.00"),
        # Per-CPU memory multiplies by the first CPU count found.
        (
            {"SLURM_MEM_PER_CPU": "512M", "SLURM_JOB_CPUS_PER_NODE": "4(x2)"},
            4 * 512 * MIB,
            "4",
        ),
        (
            {"SLURM_MEM_PER_CPU": "1g", "SLURM_CPUS_PER_TASK": "2"},
            2 * GIB,
            "2",
        ),
        # Per-node memory wins over per-CPU memory.
        (
            {
                "SLURM_MEM_PER_NODE": "1G",
                "SLURM_MEM_PER_CPU": "4G",
                "SLURM_CPUS_ON_NODE": "2",
            },
            GIB,
            "2",
        ),
        # SLURM_CPUS_ON_NODE wins over the other CPU counts.
        (
            {
                "SLURM_CPUS_ON_NODE": "5",
                "SLURM_JOB_CPUS_PER_NODE": "6",
                "SLURM_CPUS_PER_TASK": "7",
            },
            8 * GIB,
            "5",
        ),
    ],
)
def test_slurm_allocation_overrides_cgroup(
    cgroup_limited, slurm_env, mem_limit, cpu_limit
):
    inherited, output = detect(cgroup_limited, SLURM_JOB_ID="42", **slurm_env)
    assert inherited == {"MEM_LIMIT": str(mem_limit), "CPU_LIMIT": cpu_limit}
    assert "Running inside a SLURM job (Job ID: 42)" in output


@pytest.mark.parametrize(
    "slurm_env",
    [
        {"SLURM_MEM_PER_NODE": "bogus", "SLURM_CPUS_ON_NODE": "0"},
        {"SLURM_MEM_PER_NODE": "0", "SLURM_CPUS_PER_TASK": "none"},
        {"SLURM_MEM_PER_NODE": "2X"},
        # Per-CPU memory without any CPU count cannot be converted.
        {"SLURM_MEM_PER_CPU": "512"},
        {},
    ],
)
def test_malformed_slurm_values_keep_cgroup_limits(cgroup_limited, slurm_env):
    inherited, output = detect(cgroup_limited, SLURM_JOB_ID="42", **slurm_env)
    assert inherited == {"MEM_LIMIT": str(8 * GIB), "CPU_LIMIT": "4.00"}
    assert "set from SLURM" not in output


def test_slurm_allocation_applies_without_cgroup_limits(tmp_path):
    inherited, output = detect(
        tmp_path / "absent",
        SLURM_JOB_ID="42",
        SLURM_MEM_PER_NODE="3G",
        SLURM_CPUS_ON_NODE="6",
    )
    assert inherited == {"MEM_LIMIT": str(3 * GIB), "CPU_LIMIT": "6"}
    assert "Memory limit set from SLURM: 3.0G" in output
    assert "CPU limit set from SLURM: 6" in output
