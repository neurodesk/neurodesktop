"""Behavior of the neurodesk-kernel container-kernel CLI against a fake Lmod."""
import importlib.util
import json
import os
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


def container_python(marker: str) -> str:
    return textwrap.dedent(f"""\
        #!/bin/bash
        export NEURODESK_TEST_CONTAINER={marker}
        exec {sys.executable} "$@"
        """)


# Fake modules: name -> (default alias, command directory, dependencies).
MODULES = {
    "demo/2.0": ("demo", "demo_2.0", ()),
    "plain/1.0": ("", "plain_1.0", ()),
    "withdep/1.0": ("", "withdep_1.0", ("demo/2.0",)),
    "tool/1.0": ("", "tool_1.0", ()),
    "broken/1.0": ("", "broken_1.0", ()),
    "slow/1.0": ("", "slow_1.0", ()),
    "tool/1-2": ("", "tool_1-2", ()),
    "tool-1/2": ("", "tool-1_2", ()),
    "tcl/1.0": ("", "tcl_1.0", ()),
}

MODULE_FUNCTION = """\
lookup() {
    case "$1" in
%s
        *) echo "Lmod has detected the following error: unknown $1" >&2; return 1 ;;
    esac
}
module() {
    local own_name own_dir dep
    if [ "$1" = load ]; then
        lookup "$2" || return 1
        own_name=$name
        own_dir=$dir
        for dep in $deps; do module load "$dep" || return 1; done
        case ":${LOADEDMODULES:-}:" in *":$own_name:"*) return 0 ;; esac
        export PATH="$own_dir:$PATH"
        export LOADEDMODULES="${LOADEDMODULES:+$LOADEDMODULES:}$own_name"
    elif [ "$1 $2" = "--redirect show" ]; then
        lookup "$3" || return 1
        for dep in $deps; do echo "depends_on(\\"$dep\\")"; done
        case "$name" in
            # Lmod renders a Tcl modulefile's prepend-path as a table call.
            tcl/*) echo "prepend_path{\\"PATH\\",\\"$dir\\",delim=\\":\\",priority=\\"0\\"}" ;;
            *) echo "prepend_path(\\"PATH\\",\\"$dir\\")" ;;
        esac
    else
        return 1
    fi
}
"""


@pytest.fixture
def lmod(tmp_path, monkeypatch):
    """A fake Lmod whose modules expose transparent-singularity-style wrappers.

    Each container's python3 runs the test interpreter with a marker variable.
    plain and withdep shadow ipykernel_launcher so the import fails the way it
    does in a container without ipykernel; withdep also loads demo, whose
    Python does have ipykernel, as a dependency. Like Lmod, loading a module
    that is already loaded changes nothing.
    """
    containers = tmp_path / "containers"
    for directory in {entry[1] for entry in MODULES.values()}:
        write_executable(containers / directory / "python3", container_python(directory))
    write_executable(containers / "demo_2.0" / "fslpython", container_python("demo-fslpython"))

    no_ipykernel = tmp_path / "no-ipykernel"
    no_ipykernel.mkdir()
    (no_ipykernel / "ipykernel_launcher.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'ipykernel_launcher'\")\n")
    for directory in ("plain_1.0", "withdep_1.0"):
        write_executable(containers / directory / "python3", textwrap.dedent(f"""\
            #!/bin/bash
            export PYTHONPATH={no_ipykernel}
            exec {sys.executable} "$@"
            """))

    (containers / "tool_1.0" / "python3").unlink()
    write_executable(containers / "tool_1.0" / "tool", "#!/bin/bash\n")
    write_executable(containers / "broken_1.0" / "python3", textwrap.dedent("""\
        #!/bin/bash
        echo "FATAL: container image not found" >&2
        exit 255
        """))
    pid_file = tmp_path / "slow-child.pid"
    write_executable(containers / "slow_1.0" / "python3", textwrap.dedent(f"""\
        #!/bin/bash
        sleep 300 &
        echo $! > {pid_file}
        wait
        """))

    cases = "\n".join(
        f'        {name}{"|" + alias if alias else ""}) '
        f'name={name}; dir={containers / directory}; deps="{" ".join(deps)}" ;;'
        for name, (alias, directory, deps) in MODULES.items())
    module_init = tmp_path / "module.sh"
    module_init.write_text(MODULE_FUNCTION % cases)

    data_dir = tmp_path / "jupyter-data"
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(data_dir))
    monkeypatch.delenv("LOADEDMODULES", raising=False)
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


def test_tcl_modulefile_command_directory_is_found(lmod):
    assert lmod.main(["add", "tcl/1.0"]) == 0
    assert registered(lmod, "neurodesk-tcl-1.0")["argv"][1] == str(
        lmod.containers / "tcl_1.0" / "python3")


def test_module_already_loaded_in_the_calling_shell_registers(lmod, monkeypatch):
    monkeypatch.setenv("PATH", f"{lmod.containers / 'demo_2.0'}:{os.environ['PATH']}")
    monkeypatch.setenv("LOADEDMODULES", "demo/2.0")
    assert lmod.main(["add", "demo"]) == 0
    assert registered(lmod, "neurodesk-demo-2.0")["argv"][1] == str(
        lmod.containers / "demo_2.0" / "python3")


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
    assert not (lmod.kernels / "neurodesk-withdep-1.0").exists()


def test_image_python_on_path_never_stands_in_for_the_container(lmod, capsys):
    # The host python3 is on PATH, but tool/1.0 itself provides no Python.
    assert lmod.main(["add", "tool/1.0"]) == 1
    assert "provides none of: python3, python" in capsys.readouterr().err
    assert not (lmod.kernels / "neurodesk-tool-1.0").exists()


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
    assert "provides none of: fslpython" in capsys.readouterr().err
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


def test_concurrent_adds_leave_one_complete_kernel(lmod):
    results = []
    threads = [threading.Thread(target=lambda: results.append(lmod.main(["add", "demo"])))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [0, 0, 0, 0]
    spec_dir = lmod.kernels / "neurodesk-demo-2.0"
    assert [p.name for p in spec_dir.iterdir()] == ["kernel.json"]
    assert registered(lmod, "neurodesk-demo-2.0")["display_name"].endswith("(demo/2.0)")


def test_re_adding_refreshes_the_kernel_in_place(lmod):
    assert lmod.main(["add", "demo/2.0"]) == 0
    spec_file = lmod.kernels / "neurodesk-demo-2.0" / "kernel.json"
    spec_file.write_text(json.dumps({**json.loads(spec_file.read_text()), "display_name": "stale"}))
    assert lmod.main(["add", "demo"]) == 0
    assert json.loads(spec_file.read_text())["display_name"].endswith("(demo/2.0)")
    assert oct(spec_file.stat().st_mode & 0o777) == "0o644"
    assert sorted(p.name for p in spec_file.parent.iterdir()) == ["kernel.json"]
