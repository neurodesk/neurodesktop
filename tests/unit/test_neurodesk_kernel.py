"""Behavior of the neurodesk-kernel container-kernel CLI against a fake Lmod."""
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import jupyter_client.kernelspec
import jupyter_client.manager
import pytest
from testlib import repo_path


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


@pytest.fixture
def lmod(tmp_path, monkeypatch):
    """A fake Lmod whose modules expose transparent-singularity-style wrappers.

    demo/2.0 is the default version of demo and its python3 runs ipykernel with
    a marker variable, standing in for a container. plain/1.0 ships a python3
    without ipykernel, and tool/1.0 ships no Python at all.
    """
    containers = tmp_path / "containers"
    write_executable(containers / "demo_2.0" / "python3", textwrap.dedent(f"""\
        #!/bin/bash
        export NEURODESK_TEST_CONTAINER=demo_2.0
        exec {sys.executable} "$@"
        """))
    write_executable(containers / "plain_1.0" / "python3", textwrap.dedent(f"""\
        #!/bin/bash
        exec {sys.executable} -c 'raise ModuleNotFoundError("No module named ipykernel_launcher")'
        """))
    write_executable(containers / "tool_1.0" / "tool", "#!/bin/bash\n")

    module_init = tmp_path / "module.sh"
    module_init.write_text(textwrap.dedent(f"""\
        module() {{
            [ "$1" = load ] || return 1
            case "$2" in
                demo|demo/2.0) dir=demo_2.0; name=demo/2.0 ;;
                plain/1.0) dir=plain_1.0; name=plain/1.0 ;;
                tool/1.0) dir=tool_1.0; name=tool/1.0 ;;
                *) echo "Lmod has detected the following error: unknown $2" >&2; return 1 ;;
            esac
            export PATH="{containers}/$dir:$PATH"
            export LOADEDMODULES="${{LOADEDMODULES:+$LOADEDMODULES:}}$name"
        }}
        """))

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
    return cli


def test_added_kernel_runs_inside_the_module_container(lmod, capsys):
    assert lmod.main(["add", "demo/2.0"]) == 0
    assert "Registered kernel 'neurodesk-demo-2.0'" in capsys.readouterr().out

    spec = jupyter_client.kernelspec.KernelSpecManager().get_kernel_spec("neurodesk-demo-2.0")
    assert spec.language == "python"
    major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert spec.display_name == f"Python {major_minor} (demo/2.0)"

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
    spec = json.loads((lmod.kernels / "neurodesk-demo-2.0" / "kernel.json").read_text())
    assert spec["metadata"]["neurodesk_container"] == {
        "module": "demo/2.0",
        "python": str(lmod.containers / "demo_2.0" / "python3"),
    }


def test_python_without_ipykernel_is_refused_without_writing_a_kernel(lmod, capsys):
    assert lmod.main(["add", "plain/1.0"]) == 1
    error = capsys.readouterr().err
    assert "ipykernel is not installed in the container" in error
    assert str(lmod.containers / "plain_1.0" / "python3") in error
    assert "No module named ipykernel_launcher" in error
    assert not lmod.kernels.exists()


def test_image_python_on_path_never_stands_in_for_the_container(lmod, capsys):
    # The host python3 is on PATH, but tool/1.0 itself provides no Python.
    assert lmod.main(["add", "tool/1.0"]) == 1
    assert "provides none of: python3, python" in capsys.readouterr().err
    assert not lmod.kernels.exists()


def test_unknown_module_reports_how_to_find_modules(lmod, capsys):
    assert lmod.main(["add", "missing/9.9"]) == 1
    assert "could not load module 'missing/9.9'. Run 'ml av'" in capsys.readouterr().err


def test_python_option_selects_another_module_command(lmod, capsys):
    assert lmod.main(["add", "demo/2.0", "--python", "fslpython"]) == 1
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


def test_re_adding_refreshes_the_kernel_in_place(lmod):
    assert lmod.main(["add", "demo/2.0"]) == 0
    spec_file = lmod.kernels / "neurodesk-demo-2.0" / "kernel.json"
    spec_file.write_text(json.dumps({**json.loads(spec_file.read_text()), "display_name": "stale"}))
    assert lmod.main(["add", "demo"]) == 0
    assert json.loads(spec_file.read_text())["display_name"].endswith("(demo/2.0)")
    assert sorted(p.name for p in spec_file.parent.iterdir()) == ["kernel.json"]
