import importlib.util
import json
from pathlib import Path


def _load_generate_jupyter_config_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "generate_jupyter_config.py"
    spec = importlib.util.spec_from_file_location("generate_jupyter_config", module_path)
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
                    {"from": "/assets/", "to": "${base_path}assets/"}
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
        {"from": "/assets/", "to": "${base_path}assets/"}
    ]
    assert "'jamovi'" in output_config.read_text()
