import json
import ast

import pytest

from testlib import load_source_module, resolve_source


def _load_generate_jupyter_config_module():
    return load_source_module(
        "generate_jupyter_config",
        "/opt/neurodesktop/scripts/generate_jupyter_config.py",
        "scripts/generate_jupyter_config.py",
    )


def test_startup_webapps_keep_local_launchers_and_one_catalog_link():
    generator = _load_generate_jupyter_config_module()
    overlay = json.loads(resolve_source(
        "/tmp/jupyter/webapp_links.json",
        "config/jupyter/webapp_links.json",
    ).read_text())
    local_apps = {
        name: {"startup_command": f"{name} start", "port": 3000}
        for name in ("rstudio", "ezbids", "jamovi", "openrefine", "dicompare", "qsmbly")
    }
    merged = generator.merge_webapp_configs({"webapps": local_apps}, overlay)
    entries = ast.literal_eval(
        "{" + generator.generate_server_proxy_entries(merged["webapps"]) + "}"
    )

    retained_apps = set(local_apps) - {"dicompare", "qsmbly"}
    assert set(entries) == retained_apps | {"more-webapps"}
    assert set(merged["webapps"]) == set(entries)
    for name in retained_apps:
        config = local_apps[name]
        assert merged["webapps"][name]["startup_command"] == config["startup_command"]
        assert entries[name]["command"] == ["/opt/neurodesktop/webapp_launcher.sh", name]
        assert "url" not in entries[name]["launcher_entry"]

    catalog = entries["more-webapps"]
    assert catalog["launcher_entry"]["title"] == "More webapps"
    assert catalog["launcher_entry"]["url"] == "https://webapps.neurodesk.org/"
    assert catalog["launcher_entry"]["category"] == "Webapps"
    assert catalog["new_browser_tab"] is True
    assert entries["jamovi"]["timeout"] == 300


def test_overlay_removal_handles_missing_apps_without_mutating_source():
    generator = _load_generate_jupyter_config_module()
    base = {"webapps": {"dicompare": {"startup_command": "dicompare start"}}}
    overlay = {"webapps": {"dicompare": None, "qsmbly": None}}

    assert generator.merge_webapp_configs(base, overlay) == {"webapps": {}}
    assert base["webapps"]["dicompare"]["startup_command"] == "dicompare start"


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
    template_path = resolve_source(
        "/opt/neurodesktop/jupyter_notebook_config.py.template",
        "config/jupyter/jupyter_notebook_config.py.template",
    )
    return template_path


def _render_real_config(tmp_path, webapps=None):
    traitlets_config = pytest.importorskip("traitlets.config")

    generator = _load_generate_jupyter_config_module()
    webapps_json = tmp_path / "webapps.json"
    webapps_json.write_text(json.dumps({"webapps": webapps or {}}))
    output_config = tmp_path / "jupyter_notebook_config.py"

    generator.generate_config(
        webapps_json_path=webapps_json,
        # the real template: the artifact jupyter actually loads at runtime
        template_path=_real_template_path(),
        output_path=output_config,
    )

    c = traitlets_config.Config()
    exec(compile(output_config.read_text(), str(output_config), "exec"), {"c": c})
    return c


@pytest.mark.parametrize("external", [False, True], ids=["local", "external"])
@pytest.mark.parametrize("text", ["Researcher's tool", r"Research\tools"], ids=["apostrophe", "backslash"])
def test_rendered_config_preserves_webapp_metadata(tmp_path, external, text):
    app = {
        "title": text,
        "category": text,
        "icon": f"/opt/icons/{text}.svg",
        "startup_command": "research start",
        "startup_timeout": 240,
    }
    if external:
        app["direct_url"] = f"https://example.org/view?label={text}"

    c = _render_real_config(tmp_path, {"research": app})
    entry = c.ServerProxy.servers["research"]
    expected_launcher = {
        "path_info": "research",
        "title": text,
        "category": text,
        "icon_path": app["icon"],
    }
    if external:
        expected_launcher["url"] = app["direct_url"]
        assert entry["command"] == [
            "python3", "/opt/neurodesktop/external_webapp_redirect.py",
            "--url", app["direct_url"], "--port", "{port}",
        ]
        assert entry["timeout"] == 10
    else:
        assert entry["command"] == ["/opt/neurodesktop/webapp_launcher.sh", "research"]
        assert entry["unix_socket"] == "/tmp/neurodesk_webapp_research.sock"
        assert entry["timeout"] == 240
    assert entry["launcher_entry"] == expected_launcher
    assert entry["absolute_url"] is True
    assert entry["new_browser_tab"] is True


@pytest.mark.parametrize("external", [False, True], ids=["local", "external"])
@pytest.mark.parametrize("name", ["researcher's-tool", r"research\tool"], ids=["apostrophe", "backslash"])
def test_rendered_config_preserves_webapp_names(tmp_path, external, name):
    app = {"icon": "/opt/icons/research.svg"}
    if external:
        app["direct_url"] = "https://example.org/"

    c = _render_real_config(tmp_path, {name: app})
    entry = c.ServerProxy.servers[name]
    assert entry["launcher_entry"]["path_info"] == name
    assert entry["launcher_entry"]["title"] == name
    if not external:
        assert entry["command"] == ["/opt/neurodesktop/webapp_launcher.sh", name]
        assert entry["unix_socket"] == f"/tmp/neurodesk_webapp_{name}.sock"


@pytest.mark.parametrize("path", ["researcher's-api", r"research\api"], ids=["apostrophe", "backslash"])
def test_rendered_config_preserves_additional_proxy_paths(tmp_path, path):
    c = _render_real_config(tmp_path, {
        "research": {
            "icon": "/opt/icons/research.svg",
            "startup_timeout": 240,
            "additional_proxies": [{"path": path}, {"path": f"research/{path}"}],
        },
    })

    assert f"research/{path}" not in c.ServerProxy.servers
    assert c.ServerProxy.servers[path] == {
        "command": ["/opt/neurodesktop/webapp_launcher.sh", "research"],
        "unix_socket": "/tmp/neurodesk_webapp_research.sock",
        "timeout": 240,
        "absolute_url": True,
        "launcher_entry": {"enabled": False},
    }


def test_rendered_config_keeps_blocking_prometheus_exporter_disabled(tmp_path):
    """jupyter-resource-usage's Prometheus exporter runs psutil in a 1 s
    PeriodicCallback on the tornado event loop. With track_cpu_percent it
    calls cpu_percent(interval=0.05) per child process, blocking the loop
    ~50 ms x (terminals + kernels) every second, which made terminal typing
    visibly lag. The web UI indicator polls /api/metrics/v1 instead and does
    not need the exporter. Assert the rendered config keeps it disabled.
    """
    c = _render_real_config(tmp_path)

    assert c.ResourceUseDisplay.enable_prometheus_metrics is False
    # The /api/metrics/v1 path the top-bar indicator uses must stay on.
    assert c.ResourceUseDisplay.track_cpu_percent is True


def test_rendered_config_disables_broken_cpu_warning(tmp_path):
    c = _render_real_config(tmp_path)

    # jupyter-resource-usage compares CPU_LIMIT in cores directly with a
    # percentage, so ordinary CPU use otherwise turns the whole status red.
    assert c.ResourceUseDisplay.cpu_warning_threshold == 0
    assert c.ResourceUseDisplay.track_cpu_percent is True
