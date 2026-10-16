import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from testlib import repo_path


class ProjectError(Exception):
    pass


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "lightcone.engine.project",
        SimpleNamespace(ProjectError=ProjectError),
    )
    return module_at(
        "neurodesk_modules_test", repo_path("config/lightcone/neurodesk_modules.py")
    )


def declaration(root, value='modules = ["fsl/6.0.7.22"]', extra=""):
    (root / "pyproject.toml").write_text(
        '[project]\nname="test"\nlicense="MIT"\n[tool.neurodesk.lightcone]\n'
        'execution="modules"\n' + value + "\n" + extra
    )


@pytest.mark.parametrize(
    "value",
    [
        "modules=[]",
        'modules=["fsl"]',
        'modules=["fsl/latest"]',
        'modules=["fsl/1", "fsl/1"]',
        'modules="fsl/1"',
    ],
)
def test_profile_requires_exact_pins(modules, tmp_path, value):
    declaration(tmp_path, value)
    with pytest.raises(ProjectError, match="exact"):
        modules.profile(tmp_path)


def test_profile_is_explicit_committed_and_part_of_identity(modules, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="test"')
    assert modules.profile(tmp_path) is None
    declaration(tmp_path)
    first = modules.profile(tmp_path).identity()
    with pytest.raises(ProjectError, match="commit"):
        modules.profile(tmp_path, committed=True)
    declaration(tmp_path, 'modules=["fsl/6.0.7.23"]')
    assert modules.profile(tmp_path).identity() != first
    declaration(tmp_path, extra='[tool.lightcone.image]\nbase="python"')
    with pytest.raises(ProjectError, match="also declare"):
        modules.profile(tmp_path)


@pytest.fixture
def helper(tmp_path, monkeypatch, modules):
    root = tmp_path / "project"
    root.mkdir()
    declaration(root)
    for name in ("astra.yaml", "uv.lock", ".python-version"):
        (root / name).touch()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    dataset = SimpleNamespace(
        require_committer=lambda root: None, status=lambda root: []
    )
    project = SimpleNamespace(ProjectError=ProjectError, license_of=lambda root: "MIT")
    full_id = "clu_owned-full-identity"

    def decode(value):
        if value != full_id:
            raise ValueError("expected full identity")

    monkeypatch.setitem(
        sys.modules, "lightcone", SimpleNamespace(neurodesk_modules=modules)
    )
    monkeypatch.setitem(
        sys.modules,
        "lightcone.engine",
        SimpleNamespace(dataset=dataset, project=project),
    )
    monkeypatch.setitem(
        sys.modules,
        "lightcone.engine.compute.model",
        SimpleNamespace(Identity=SimpleNamespace(decode=decode)),
    )
    monkeypatch.setattr(
        modules,
        "module_environment",
        lambda declared, root: {"LOADEDMODULES": ":".join(declared.modules)},
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls.jsonl"
    (binary / "lc").write_text(f"""#!{sys.executable}
import json, os, sys
args=sys.argv[1:]
with open({str(log)!r}, "a") as stream: stream.write(json.dumps(args)+"\\n")
operation=args[1] if args[0]=="compute" else args[0]
if operation=="launch": print(json.dumps({{"id": os.environ.get("RETURN_ID", {full_id!r})}}))
sys.exit(7 if operation==os.environ.get("FAIL_OPERATION") else 0)
""")
    (binary / "lc").chmod(0o755)
    helper = module_at(
        "lightcone_materialize_test", repo_path("config/slurm/lightcone_materialize.py")
    )
    monkeypatch.setattr(
        helper,
        "sys",
        SimpleNamespace(executable=str(binary / "python"), stderr=sys.stderr),
    )
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    args = SimpleNamespace(
        project=str(root),
        cpus=2,
        memory=8,
        time="2h",
        timeout=10,
        refresh=False,
        targets=["baseline/brain"],
    )
    return helper, args, root, log, full_id, dataset


@pytest.mark.parametrize(
    "fail,operations",
    [
        (None, ["launch", "status", "materialize", "down"]),
        ("launch", ["launch", "down"]),
        ("status", ["launch", "status", "down"]),
        ("materialize", ["launch", "status", "materialize", "down"]),
        ("down", ["launch", "status", "materialize", "down"]),
    ],
)
def test_owned_allocation_cleanup_and_failure_propagation(
    helper, monkeypatch, fail, operations
):
    module, args, root, log, identity, _ = helper
    if fail:
        monkeypatch.setenv("FAIL_OPERATION", fail)
    assert module.materialize(args) == (7 if fail else 0)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [
        argv[1] if argv[0] == "compute" else argv[0] for argv in calls
    ] == operations
    assert calls[-1] == ["compute", "down", identity, "--json"]
    if not fail:
        assert ["materialize", identity, "baseline/brain"] in calls
    assert not (root / "status.json").exists()


@pytest.mark.parametrize(
    "refusal", ["dirty", "competing", "nested", "uncommitted", "image"]
)
def test_refuses_before_allocating(helper, monkeypatch, refusal):
    module, args, root, log, _, dataset = helper
    if refusal == "dirty":
        dataset.status = lambda root: ["dirty"]
    elif refusal == "competing":
        (root / "status.json").write_text("{}")
    elif refusal == "nested":
        monkeypatch.setenv("SLURM_JOB_ID", "123")
    elif refusal == "uncommitted":
        declaration(root, 'modules=["fsl/6.0.7.23"]')
    else:
        declaration(root, extra='[tool.lightcone.image]\nbase="python"')
    with pytest.raises(ProjectError):
        module.materialize(args)
    assert not log.exists()


def test_unknown_launch_identity_never_releases_someone_elses_cluster(
    helper, monkeypatch
):
    module, args, _, log, _, _ = helper
    monkeypatch.setenv("RETURN_ID", "friendly-name")
    with pytest.raises(ValueError, match="full identity"):
        module.materialize(args)
    assert len(log.read_text().splitlines()) == 1


def test_install_keeps_module_patch_separate_from_removed_callback():
    dockerfile = repo_path("Dockerfile").read_text()
    install = dockerfile.split('ARG LIGHTCONE_CLI_VERSION="0.5.0"')[1].split(
        "# Neurodesk's FreeBrowse"
    )[0]
    assert "ebac3ebd1cf5e5466c38af54286590fedbe5c6ba3dede3bd204e53bb7d6c3bfd" in install
    assert "patch_modules.py" in install
    assert "snakemake" not in install
    assert "packaging==" not in install
    assert not repo_path("config/agents/patch_lightcone_cli.py").exists()


def test_interrupted_readiness_still_releases_owned_allocation(helper, monkeypatch):
    module, args, _, log, identity, _ = helper
    real_run = subprocess.run

    def interrupted(argv, **kwargs):
        if argv[1:3] == ["compute", "status"]:
            raise KeyboardInterrupt
        return real_run(argv, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        module.materialize(args)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1] == ["compute", "down", identity, "--json"]


def test_module_policy_retains_private_home_and_gpu_mask(
    modules, tmp_path, monkeypatch
):
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Policy:
        env: dict
        tmp_home: object

    runtime = SimpleNamespace(root=tmp_path, env_dir=tmp_path / ".venv")
    ordinary = Policy(
        {
            "HOME": "/private/task",
            "CUDA_VISIBLE_DEVICES": "",
            "TMPDIR": "/private/task/tmp",
        },
        tmp_path / "task",
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname="test"')
    assert modules.policy_for(runtime, ordinary) is ordinary
    declared = modules.ModuleProfile(("fsl/6.0.7.22",))
    monkeypatch.setattr(modules, "profile", lambda *args, **kwargs: declared)
    monkeypatch.setattr(
        modules,
        "module_environment",
        lambda *args: {
            "PATH": "/external/bin:/usr/bin",
            "HOME": "/user/home",
            "BASH_ENV": "/untrusted",
            "CUDA_VISIBLE_DEVICES": "0,1",
        },
    )
    policy = modules.policy_for(runtime, ordinary)
    assert policy.env["PATH"] == f"{runtime.env_dir}/bin:/external/bin:/usr/bin"
    assert policy.env["HOME"] == "/private/task"
    assert policy.env["CUDA_VISIBLE_DEVICES"] == ""
    assert policy.env["BASH_ENV"] == ""
    assert policy.env["APPTAINER_HOME"] == str(ordinary.tmp_home)


def test_patch_refuses_source_drift_before_writing(tmp_path):
    patcher = module_at(
        "lightcone_patch_test", repo_path("config/lightcone/patch_modules.py")
    )
    engine = tmp_path / "src/lightcone/engine"
    engine.mkdir(parents=True)
    path = engine / "container.py"
    path.write_text("changed upstream")
    with pytest.raises(ValueError, match="source drift"):
        patcher.patch(tmp_path)
    assert path.read_text() == "changed upstream"


def test_profile_refusal_cleans_allocated_task_home(modules, tmp_path):
    declaration(tmp_path)
    home = tmp_path / "task-home"
    home.mkdir()
    runtime = SimpleNamespace(root=tmp_path)
    ordinary = SimpleNamespace(tmp_home=home)
    with pytest.raises(ProjectError, match="commit"):
        modules.policy_for(runtime, ordinary)
    assert not home.exists()
