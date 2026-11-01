"""Behavior of the neurodesk-kernel container-kernel CLI against a fake Lmod."""
import contextlib
import importlib.util
import json
import os
import shlex
import sys
import textwrap
import threading
import time
from pathlib import Path

import jupyter_client.kernelspec
import jupyter_client.manager
import pytest
from testlib import repo_path

MAJOR_MINOR = f"{sys.version_info.major}.{sys.version_info.minor}"


def load_cli():
    spec = importlib.util.spec_from_file_location(
        "neurodesk_kernel", repo_path("config/jupyter/neurodesk_kernel.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def container_wrapper(image: str, runtime: str) -> str:
    """A transparent-singularity-style wrapper that runs *image*.

    *runtime* is the body of neurodesk_container, which receives the
    arguments after the command name.
    """
    return textwrap.dedent("""\
        #!/bin/bash
        # neurodesk-artifact-v2
        neurodesk_container() {
            shift 3
        %s
        }
        neurodesk_container exec "$(dirname "$0")/"%s.simg python3 "$@"
        """) % (textwrap.indent(textwrap.dedent(runtime), "    "), image)


def container_python(image: str, marker: str, pythonpath: str = "") -> str:
    exports = f"export NEURODESK_TEST_CONTAINER={shlex.quote(marker)}\n"
    if pythonpath:
        exports += f"export PYTHONPATH={shlex.quote(pythonpath)}\n"
    return container_wrapper(image, exports + f'exec {sys.executable} "$@"\n')


# Fake Lmod: dependencies load first, a reload is a no-op, and each prepend
# lands in front of the previous one, as with Lmod.
MODULE_FUNCTION = r"""
C=@CONTAINERS@
own() {  # own <name> <dir>...: prepend each dir and record the module
    local name=$1; shift
    case ":${LOADEDMODULES:-}:" in *":$name:"*) return 0 ;; esac
    local dir; for dir in "$@"; do export PATH="$dir:$PATH"; done
    export LOADEDMODULES="${LOADEDMODULES:+$LOADEDMODULES:}$name"
}
module() {
    [ "$1" = load ] || return 1
    case "$2" in
        demo|demo/2.0) own demo/2.0 "$C/demo_2.0" ;;
        plain/1.0) own plain/1.0 "$C/plain_1.0" ;;
        withdep/1.0) module load demo/2.0; own withdep/1.0 "$C/withdep_1.0" ;;
        meta/1.0) module load demo/2.0; own meta/1.0 ;;
        tool/1.0) own tool/1.0 "$C/tool_1.0" ;;
        broken/1.0) own broken/1.0 "$C/broken_1.0" ;;
        slow/1.0) own slow/1.0 "$C/slow_1.0" ;;
        tool/1-2) own tool/1-2 "$C/tool_1-2" ;;
        tool-1/2) own tool-1/2 "$C/tool-1_2" ;;
        multi/1.0) own multi/1.0 "$C/multi_first" "$C/multi_second" ;;
        mixed/1.0) own mixed/1.0 "$C/mixed_1.0" ;;
        hostlink/1.0) own hostlink/1.0 "$C/hostlink_1.0" ;;
        fsl_arm64/6.0) own fsl_arm64/6.0 "$C/fsl_arm64_6.0" ;;
        *) echo "Lmod has detected the following error: unknown $2" >&2; return 1 ;;
    esac
}
"""


@pytest.fixture
def lmod(tmp_path, monkeypatch):
    """A fake Lmod whose modules expose transparent-singularity-style wrappers.

    Each wrapper names the image of the module it belongs to, and its runtime
    runs the test interpreter with a marker variable naming its directory.
    plain and withdep shadow ipykernel_launcher so the import fails the way it
    does in a container without ipykernel; withdep and meta load demo, whose
    Python does have ipykernel.
    """
    containers = tmp_path / "containers"
    no_ipykernel = tmp_path / "no-ipykernel"
    no_ipykernel.mkdir()
    (no_ipykernel / "ipykernel_launcher.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'ipykernel_launcher'\")\n")

    def python(directory, image, pythonpath=""):
        write_executable(containers / directory / "python3",
                         container_python(image, directory, pythonpath))

    python("demo_2.0", "demo_2.0_20260101")
    write_executable(containers / "demo_2.0" / "fslpython", container_python(
        "demo_2.0_20260101", "demo-fslpython").replace(" python3 ", " fslpython "))
    python("plain_1.0", "plain_1.0_20260101", str(no_ipykernel))
    python("withdep_1.0", "withdep_1.0_20260101", str(no_ipykernel))
    python("tool_1-2", "tool_1-2_20260101")
    python("tool-1_2", "tool-1_2_20260101")
    python("multi_first", "multi_1.0_20260101")
    python("multi_second", "multi_1.0_20260101")
    python("fsl_arm64_6.0", "fsl_arm64_6.0_20260101")
    # A wrapper in mixed's own directory that runs a different container.
    python("mixed_1.0", "other_9.9_20260101")
    write_executable(containers / "tool_1.0" / "tool", "#!/bin/bash\n")
    write_executable(containers / "broken_1.0" / "python3", container_wrapper(
        "broken_1.0_20260101", """\
        echo "FATAL: container image not found" >&2
        exit 255
        """))
    pid_file = tmp_path / "slow-child.pid"
    # The wrapper exits on SIGTERM, but its descendant ignores SIGTERM and holds
    # no pipe, like a stuck container runtime.
    write_executable(containers / "slow_1.0" / "python3", container_wrapper(
        "slow_1.0_20260101", f"""\
        ( trap '' TERM; exec sleep 300 ) </dev/null >/dev/null 2>&1 &
        echo $! > {pid_file}
        sleep 300
        """))
    # The image's own Python placed in a module directory is not a container.
    (containers / "hostlink_1.0").mkdir()
    (containers / "hostlink_1.0" / "python3").symlink_to(sys.executable)

    module_init = tmp_path / "module.sh"
    module_init.write_text(MODULE_FUNCTION.replace("@CONTAINERS@", shlex.quote(str(containers))))

    data_dir = tmp_path / "jupyter-data"
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(data_dir))
    monkeypatch.delenv("LOADEDMODULES", raising=False)
    # Put the image's own Python first on PATH, as in the Neurodesktop image.
    monkeypatch.setenv("PATH", f"{os.path.dirname(sys.executable)}:{os.environ['PATH']}")
    cli = load_cli()
    monkeypatch.setattr(cli, "MODULE_INIT", str(module_init))
    monkeypatch.setattr(cli, "ENVIRONMENT_SCRIPT", str(tmp_path / "absent.sh"))
    # The image installs the wrapper with mode 0755; the checkout copy is 0644.
    kernel_wrapper = write_executable(
        tmp_path / "kernel_wrapper.sh",
        repo_path("config/jupyter/kernel_wrapper.sh").read_text())
    monkeypatch.setattr(cli, "KERNEL_WRAPPER", str(kernel_wrapper))
    cli.kernels = data_dir / "kernels"
    cli.containers = containers
    cli.pid_file = pid_file
    return cli


def registered(cli, name):
    return json.loads((cli.kernels / name / "kernel.json").read_text())


def wrapper_for(cli, module):
    assert cli.main(["add", module]) == 0
    return registered(cli, cli.kernel_name_for(module))["argv"][1]


def test_added_kernel_runs_inside_the_module_container(lmod, capsys):
    assert lmod.main(["add", "demo/2.0"]) == 0
    assert "Registered kernel 'neurodesk-demo-2.0'" in capsys.readouterr().out

    spec = jupyter_client.kernelspec.KernelSpecManager().get_kernel_spec("neurodesk-demo-2.0")
    assert spec.language == "python"
    assert spec.display_name == f"Python {MAJOR_MINOR} (demo/2.0)"

    manager, client = jupyter_client.manager.start_new_kernel(
        kernel_name="neurodesk-demo-2.0", startup_timeout=60)
    try:
        output = []
        reply = client.execute_interactive(
            "import os; print(os.environ.get('NEURODESK_TEST_CONTAINER'))",
            output_hook=lambda msg: output.append(msg["content"].get("text", "")),
            timeout=30)
        assert reply["content"]["status"] == "ok"
        assert "".join(output).strip() == "demo_2.0"
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)


def test_bare_module_name_registers_the_loaded_default_version(lmod):
    assert lmod.main(["add", "demo"]) == 0
    assert registered(lmod, "neurodesk-demo-2.0")["metadata"]["neurodesk_container"] == {
        "module": "demo/2.0",
        "python": str(lmod.containers / "demo_2.0" / "python3"),
    }


def test_module_already_loaded_in_the_calling_shell_registers(lmod, monkeypatch):
    monkeypatch.setenv("PATH", f"{lmod.containers / 'demo_2.0'}:{os.environ['PATH']}")
    monkeypatch.setenv("LOADEDMODULES", "demo/2.0")
    assert wrapper_for(lmod, "demo/2.0") == str(lmod.containers / "demo_2.0" / "python3")


def test_later_prepend_takes_precedence_like_the_loaded_path(lmod):
    assert wrapper_for(lmod, "multi/1.0") == str(lmod.containers / "multi_second" / "python3")


def test_architecture_variant_module_matches_its_image(lmod):
    assert wrapper_for(lmod, "fsl_arm64/6.0") == str(
        lmod.containers / "fsl_arm64_6.0" / "python3")


def test_python_without_ipykernel_is_refused_without_writing_a_kernel(lmod, capsys):
    assert lmod.main(["add", "plain/1.0"]) == 1
    error = capsys.readouterr().err
    assert "ipykernel is not installed in the container" in error
    assert (f"{lmod.containers / 'plain_1.0' / 'python3'}: Python {MAJOR_MINOR} runs, "
            "but importing ipykernel failed: No module named 'ipykernel_launcher'") in error
    assert not (lmod.kernels / "neurodesk-plain-1.0").exists()


def test_a_dependency_python_never_stands_in_for_the_requested_module(lmod, capsys):
    # withdep loads demo, whose python3 has ipykernel; withdep's own does not.
    assert lmod.main(["add", "withdep/1.0"]) == 1
    error = capsys.readouterr().err
    assert "ipykernel is not installed in the container" in error
    assert "demo_2.0" not in error
    # meta has no container of its own; only its dependency has a Python.
    assert lmod.main(["add", "meta/1.0"]) == 1
    assert ("module 'meta/1.0' provides no python3 or python wrapper for its container "
            "(meta_1.0_*)") in capsys.readouterr().err
    assert not lmod.kernels.exists()


def test_wrapper_for_another_container_in_the_module_directory_is_ignored(lmod, capsys):
    assert lmod.main(["add", "mixed/1.0"]) == 1
    assert "provides no python3 or python wrapper for its container (mixed_1.0_*)" in (
        capsys.readouterr().err)
    assert not lmod.kernels.exists()


def test_image_python_never_stands_in_for_the_container(lmod, capsys):
    # The image's own python3 is first on PATH, and tool/1.0 provides none.
    assert lmod.main(["add", "tool/1.0"]) == 1
    assert "provides no python3 or python wrapper" in capsys.readouterr().err
    # hostlink/1.0 puts the image's own interpreter in its own directory.
    assert lmod.main(["add", "hostlink/1.0"]) == 1
    assert "provides no python3 or python wrapper" in capsys.readouterr().err
    assert not lmod.kernels.exists()


def test_container_launch_failure_is_not_reported_as_missing_ipykernel(lmod, capsys):
    assert lmod.main(["add", "broken/1.0"]) == 1
    error = capsys.readouterr().err
    assert "could not run Python from module 'broken/1.0'" in error
    assert "could not start: FATAL: container image not found" in error
    assert "ipykernel" not in error


def test_probe_timeout_terminates_the_container_process_tree(lmod, monkeypatch, capsys):
    monkeypatch.setattr(lmod, "PROBE_TIMEOUT_SECONDS", 2)
    assert lmod.main(["add", "slow/1.0"]) == 1
    assert "timed out after 2 s" in capsys.readouterr().err
    child = int(lmod.pid_file.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, 9)
        pytest.fail("the probe's descendant process outlived the timeout")


def test_unknown_module_reports_how_to_find_modules(lmod, capsys):
    assert lmod.main(["add", "missing/9.9"]) == 1
    assert "could not load module 'missing/9.9'. Run 'ml av'" in capsys.readouterr().err
    assert not lmod.kernels.exists()


def test_python_option_selects_another_module_command(lmod, capsys):
    assert lmod.main(["add", "demo/2.0", "--python", "fslpython"]) == 0
    assert registered(lmod, "neurodesk-demo-2.0")["argv"][1] == str(
        lmod.containers / "demo_2.0" / "fslpython")
    assert lmod.main(["add", "plain/1.0", "--python", "fslpython"]) == 1
    assert "provides no fslpython wrapper" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        lmod.main(["add", "demo/2.0", "--python", "/usr/bin/python3"])


def test_foreign_kernel_with_the_same_name_is_left_alone(lmod, capsys):
    foreign = lmod.kernels / "neurodesk-demo-2.0" / "kernel.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(json.dumps({"argv": ["python3"], "display_name": "mine"}))

    assert lmod.main(["add", "demo/2.0"]) == 1
    assert "did not create" in capsys.readouterr().err
    assert json.loads(foreign.read_text())["display_name"] == "mine"

    assert lmod.main(["remove", "neurodesk-demo-2.0"]) == 1
    assert foreign.exists()


def test_symlinked_kernel_directory_is_neither_written_nor_removed(lmod, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    assert lmod.main(["add", "demo/2.0"]) == 0
    (lmod.kernels / "neurodesk-demo-2.0").rename(elsewhere)
    (lmod.kernels / "neurodesk-demo-2.0").symlink_to(elsewhere)
    before = (elsewhere / "kernel.json").read_text()

    assert lmod.main(["add", "demo/2.0"]) == 1
    assert "did not create" in capsys.readouterr().err
    assert lmod.main(["remove", "demo/2.0"]) == 1
    assert (elsewhere / "kernel.json").read_text() == before


def test_colliding_kernel_names_never_replace_or_remove_another_module(lmod, capsys):
    assert lmod.main(["add", "tool/1-2"]) == 0
    assert lmod.main(["add", "tool-1/2"]) == 1
    assert ("kernel name 'neurodesk-tool-1-2' is already used by module 'tool/1-2'"
            in capsys.readouterr().err)
    assert lmod.main(["remove", "tool-1/2"]) == 1
    assert registered(lmod, "neurodesk-tool-1-2")["metadata"]["neurodesk_container"][
        "module"] == "tool/1-2"


def test_list_and_remove_manage_only_container_kernels(lmod, capsys):
    other = lmod.kernels / "my-env" / "kernel.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"argv": ["python3"], "display_name": "my env"}))
    assert lmod.main(["add", "demo/2.0"]) == 0
    capsys.readouterr()

    assert lmod.main(["list"]) == 0
    listing = capsys.readouterr().out
    assert "neurodesk-demo-2.0\tdemo/2.0\t" in listing
    assert "my-env" not in listing

    (lmod.containers / "demo_2.0" / "python3").unlink()
    lmod.main(["list"])
    assert "[container not available]" in capsys.readouterr().out

    assert lmod.main(["remove", "demo/2.0"]) == 0
    assert not (lmod.kernels / "neurodesk-demo-2.0").exists()
    assert other.exists()
    assert lmod.main(["remove", "demo/2.0"]) == 1
    assert "no container kernel matches 'demo/2.0'" in capsys.readouterr().err


def test_writes_wait_for_another_invocation_holding_the_kernels_lock(lmod, monkeypatch):
    real_lock = lmod.kernels_lock
    requested = threading.Event()

    @contextlib.contextmanager
    def observed_lock():
        requested.set()
        with real_lock():
            yield

    monkeypatch.setattr(lmod, "kernels_lock", observed_lock)
    results = []
    adder = threading.Thread(target=lambda: results.append(lmod.main(["add", "demo"])))
    with real_lock():
        adder.start()
        assert requested.wait(timeout=120), "add never asked for the lock"
        # Writing takes milliseconds once the lock is granted.
        adder.join(timeout=2)
        assert adder.is_alive(), "add wrote while another invocation held the lock"
        assert not (lmod.kernels / "neurodesk-demo-2.0").exists()
    adder.join(timeout=30)
    assert results == [0]
    spec_dir = lmod.kernels / "neurodesk-demo-2.0"
    assert [p.name for p in spec_dir.iterdir()] == ["kernel.json"]
    assert registered(lmod, "neurodesk-demo-2.0")["display_name"].endswith("(demo/2.0)")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unwritable_kernels_directory_is_reported_without_a_traceback(lmod, capsys):
    assert lmod.main(["add", "demo"]) == 0
    lock = lmod.kernels / ".neurodesk-kernel.lock"
    lock.chmod(0o444)
    try:
        assert lmod.main(["remove", "demo/2.0"]) == 1
    finally:
        lock.chmod(0o644)
    error = capsys.readouterr().err
    assert f"Permission denied: {lock}. Check the permissions" in error
    assert "Traceback" not in error


def test_re_adding_refreshes_the_kernel_in_place(lmod):
    assert lmod.main(["add", "demo/2.0"]) == 0
    spec_file = lmod.kernels / "neurodesk-demo-2.0" / "kernel.json"
    spec_file.write_text(json.dumps({**json.loads(spec_file.read_text()), "display_name": "stale"}))
    assert lmod.main(["add", "demo"]) == 0
    assert json.loads(spec_file.read_text())["display_name"].endswith("(demo/2.0)")
    assert oct(spec_file.stat().st_mode & 0o777) == "0o644"
    assert sorted(p.name for p in spec_file.parent.iterdir()) == ["kernel.json"]
