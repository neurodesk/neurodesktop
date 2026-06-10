import importlib.util
import json
from pathlib import Path


def _load_generate_jupyter_config_module():
    repo_root = Path(__file__).resolve().parents[1]
    candidates = (
        repo_root / "scripts" / "generate_jupyter_config.py",
        Path("/opt/neurodesktop/scripts/generate_jupyter_config.py"),
    )
    module_path = next((candidate for candidate in candidates if candidate.exists()), None)
    assert module_path is not None, f"generate_jupyter_config.py not found in: {candidates}"
    spec = importlib.util.spec_from_file_location("generate_jupyter_config", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_config_writes_merged_webapp_config_for_wrapper(tmp_path):
    generator = _load_generate_jupyter_config_module()

    webapps_json = tmp_path / "webapps.json"
    overlay_json = tmp_path / "overlay.json"
    template = tmp_path / "jupyter_notebook_config.py.template"
    output_config = tmp_path / "jupyter_notebook_config.py"
    merged_output = tmp_path / "merged-webapps.json"

    webapps_json.write_text(json.dumps({
        "webapps": {
            "jamovi": {
                "title": "jamovi",
                "icon": "/opt/neurodesk_brain_icon.svg",
                "startup_command": "jamovi start",
                "port": 41337,
            }
        }
    }))
    overlay_json.write_text(json.dumps({
        "webapps": {
            "jamovi": {
                "startup_timeout": 300,
                "path_rewrites": [
                    {"from": "/assets/", "to": "${base_path}assets/"},
                    {"from": "\"/version\"", "to": "\"${base_path}version\""},
                    {"from": "\"/settings\"", "to": "\"${base_path}settings\""},
                ]
            }
        }
    }))
    template.write_text("c.ServerProxy.servers = {\n  'neurodesktop': {}\n# {{WEBAPP_SERVERS}}\n}\n")

    generator.generate_config(
        webapps_json,
        template,
        output_config,
        [overlay_json],
        merged_output,
    )

    merged = json.loads(merged_output.read_text())
    assert merged["webapps"]["jamovi"]["startup_command"] == "jamovi start"
    assert merged["webapps"]["jamovi"]["startup_timeout"] == 300
    assert merged["webapps"]["jamovi"]["path_rewrites"] == [
        {"from": "/assets/", "to": "${base_path}assets/"},
        {"from": "\"/version\"", "to": "\"${base_path}version\""},
        {"from": "\"/settings\"", "to": "\"${base_path}settings\""},
    ]
    assert "'jamovi'" in output_config.read_text()


def _real_template_path():
    repo_root = Path(__file__).resolve().parents[1]
    candidates = (
        repo_root / "config" / "jupyter" / "jupyter_notebook_config.py.template",
        Path("/opt/neurodesktop/jupyter_notebook_config.py.template"),
    )
    template_path = next((c for c in candidates if c.exists()), None)
    assert template_path is not None, f"template not found in: {candidates}"
    return template_path


def test_rendered_config_keeps_blocking_prometheus_exporter_disabled(tmp_path):
    """jupyter-resource-usage's Prometheus exporter runs psutil in a 1 s
    PeriodicCallback on the tornado event loop. With track_cpu_percent it
    calls cpu_percent(interval=0.05) per child process, blocking the loop
    ~50 ms x (terminals + kernels) every second, which made terminal typing
    visibly lag. The web UI indicator polls /api/metrics/v1 instead and does
    not need the exporter. Assert the rendered config keeps it disabled.
    """
    import pytest

    traitlets_config = pytest.importorskip("traitlets.config")

    generator = _load_generate_jupyter_config_module()
    webapps_json = tmp_path / "webapps.json"
    webapps_json.write_text(json.dumps({"webapps": {}}))
    output_config = tmp_path / "jupyter_notebook_config.py"

    generator.generate_config(
        webapps_json_path=webapps_json,
        # the real template: the artifact jupyter actually loads at runtime
        template_path=_real_template_path(),
        output_path=output_config,
    )

    c = traitlets_config.Config()
    exec(compile(output_config.read_text(), str(output_config), "exec"), {"c": c})

    assert c.ResourceUseDisplay.enable_prometheus_metrics is False
    # The /api/metrics/v1 path the top-bar indicator uses must stay on.
    assert c.ResourceUseDisplay.track_cpu_percent is True
