"""Installed workspace links must open rendered documents in JupyterLab."""

import json
from pathlib import Path

import os
import subprocess
import time
import urllib.request

import pytest

from test_rise_slides_image import _BidiSession, _unused_port, _wait_for_server, _stop

from testlib import run_cmd


LABEXTENSION = Path(
    "/opt/conda/share/jupyter/labextensions/neurodesk-launcher"
)


def bundle_text():
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (LABEXTENSION / "static").glob("*.js")
    )


def test_workspace_link_plugin_survived_the_labextension_build():
    text = bundle_text()

    assert "neurodesk-launcher:workspace-links" in text
    # The launcher plugin must still be there: both are exported as one array,
    # and a mistake there silently drops one of them.
    assert "neurodesk-launcher:plugin" in text


def test_neither_rendering_viewer_is_disabled_in_page_config():
    disabled = json.loads(
        Path(
            "/opt/jovyan_defaults/.jupyter/labconfig/page_config.json"
        ).read_text()
    ).get("disabledExtensions", {})

    for extension in (
        "@jupyterlab/markdownviewer-extension",
        "@jupyterlab/htmlviewer-extension",
    ):
        assert not any(
            key == extension or key.startswith(f"{extension}:")
            for key in disabled
        ), extension


def test_jupyterlab_accepts_the_rebuilt_extension():
    code, output = run_cmd("jupyter labextension list --verbose", timeout=60)

    assert code == 0, output
    assert "neurodesk-launcher" in output
    neurodesk_lines = [
        line for line in output.splitlines() if "neurodesk-launcher" in line
    ]
    assert neurodesk_lines, output
    for line in neurodesk_lines:
        assert "not compatible" not in line, line



def evaluate(bidi, context, expression):
    result = bidi.request("script.evaluate", {
        "expression": expression, "target": {"context": context}, "awaitPromise": True,
    })
    assert result["type"] == "success", result
    return result.get("result", {}).get("value")


def check_slurm_launcher(bidi, context):
    """Use the installed command metadata and click the tile after re-rendering."""
    evaluate(bidi, context, """Promise.all([
        window.jupyterapp.activatePlugin('neurodesk-launcher:plugin'),
        window.jupyterapp.activatePlugin('jupyterlab-slurm:plugin')
    ]).then(() => true)""")
    tile = """Array.from(window.jupyterapp.shell.currentWidget.node.querySelectorAll('.jp-Launcher-section'))
        .find(section => section.querySelector('.jp-Launcher-sectionTitle')?.textContent.trim() === 'Neurodesk')
        ?.querySelector('.jp-SlurmWidget-NerscLaunchIcon')
        ?.closest('.jp-LauncherCard')"""
    visible = """Array.from(document.querySelectorAll('.jp-SlurmWidget'))
        .some(node => node.checkVisibility())"""
    for _ in range(2):
        evaluate(bidi, context,
                 "window.jupyterapp.commands.execute('launcher:create').then(() => true)")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if evaluate(bidi, context, f"Boolean({tile})"):
                break
            time.sleep(.2)
        assert evaluate(bidi, context, f"Boolean({tile})"), "Slurm launcher icon missing"
        assert evaluate(bidi, context, f"({tile}).textContent.includes('Slurm Dashboard')")
        assert not evaluate(bidi, context, visible), "New launcher should hide the previous dashboard"
        point = json.loads(evaluate(bidi, context, f"""JSON.stringify((() => {{
            const node = {tile};
            node.scrollIntoView({{block: 'center'}});
            const rect = node.getBoundingClientRect();
            return {{x: Math.round(rect.left + rect.width / 2),
                     y: Math.round(rect.top + rect.height / 2)}};
        }})())"""))
        bidi.request("input.performActions", {
            "context": context,
            "actions": [{"type": "pointer", "id": "slurm-mouse",
                         "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "origin": "viewport", **point},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ]}],
        })
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if evaluate(bidi, context, visible):
                break
            time.sleep(.2)
        assert evaluate(bidi, context, visible), (
            "Slurm tile did not show dashboard: "
            + str(evaluate(bidi, context, "document.body.innerText"))[-2000:]
        )


@pytest.mark.parametrize("base", ["/", "/user/workspace-test/"])
def test_clicking_workspace_links_opens_rendered_documents(tmp_path, base):
    (tmp_path / "a report.md").write_text("# Workspace link opened\n")
    (tmp_path / "report.html").write_text("<h1>HTML workspace report</h1>")
    server_port, browser_port = _unused_port(), _unused_port()
    prefix = f"http://127.0.0.1:{server_port}{base}"
    token = "workspace-links-test"
    environment = {**os.environ, "HOME": str(tmp_path)}
    # Slurm initialization overwrites flat Tornado settings from this traitlet.
    # Keep this browser test independent of a host controller in HPC mode.
    server_config = tmp_path / "jupyter_server_config.py"
    server_config.write_text('c = get_config()\nc.SlurmCommandPaths.squeue_path = "/usr/bin/true"\n')
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    log_path = tmp_path / "jupyter.log"
    with log_path.open("w") as log, (tmp_path / "firefox.log").open("w") as browser_log:
        server = subprocess.Popen([
            "jupyter", "lab", "--no-browser", "--ServerApp.allow_root=True",
            f"--config={server_config}",
            "--LabApp.expose_app_in_browser=True", f"--ServerApp.port={server_port}",
            "--ServerApp.port_retries=0", f"--ServerApp.base_url={base}",
            f"--ServerApp.root_dir={tmp_path}", f"--FileContentsManager.preferred_dir={tmp_path}",
            f"--IdentityProvider.token={token}",
            '--ServerApp.jpserver_extensions={"jupyterlab":True,"neurodesk_t3_code":False}',
        ], env=environment, stdout=log, stderr=subprocess.STDOUT)
        browser = None
        bidi = None
        try:
            browser = subprocess.Popen([
                "/usr/bin/firefox", "--headless", "--profile", str(profile),
                f"--remote-debugging-port={browser_port}", "about:blank",
            ], env=environment, stdout=browser_log, stderr=subprocess.STDOUT)
            _wait_for_server(prefix + "api/status?token=" + token, server, log_path)
            with urllib.request.urlopen(
                prefix + "jupyterlab_slurm/squeue?token=" + token, timeout=10
            ) as response:
                queue = json.load(response)
            assert queue["success"] and queue["data"]["rows"] == [], queue
            assert queue["responseMessage"].startswith("Success: /usr/bin/true "), queue
            bidi = _BidiSession(f"ws://127.0.0.1:{browser_port}/session", browser)
            bidi.request("session.new", {"capabilities": {}})
            context = bidi.request("browsingContext.create", {"type": "tab"})["context"]
            bidi.request("browsingContext.navigate", {
                "context": context, "url": prefix + "lab?token=" + token, "wait": "interactive",
            })
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if evaluate(bidi, context, "Boolean(window.jupyterapp?.started)"):
                    break
                time.sleep(.2)
            assert evaluate(bidi, context, "Boolean(window.jupyterapp?.started)"), "Lab did not start"
            evaluate(bidi, context, "window.jupyterapp.started.then(() => true)")
            # Explicit activation waits for this auto-start plugin's dependencies.
            evaluate(bidi, context,
                     "window.jupyterapp.activatePlugin('neurodesk-launcher:workspace-links').then(() => true)")
            check_slurm_launcher(bidi, context)
            original_url = evaluate(bidi, context, "location.href")
            for name, reference, rendered in [
                ("a report.md", ":12", "Boolean(window.jupyterapp.shell.currentWidget?.node.querySelector('h1')?.textContent.includes('Workspace link opened'))"),
                ("report.html", "", "Boolean(window.jupyterapp.shell.currentWidget?.node.querySelector('iframe'))"),
            ]:
                target = str(tmp_path / name) + reference
                evaluate(bidi, context, """(() => {
                    const link = document.createElement('a');
                    link.href = """ + json.dumps(target) + """;
                    link.textContent = 'Open report';
                    document.body.append(link);
                    link.click();
                    link.remove();
                    return true;
                })()""")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    opened = evaluate(bidi, context,
                        "window.jupyterapp.shell.currentWidget?.context?.path === " + json.dumps(name))
                    if opened and evaluate(bidi, context, rendered):
                        break
                    time.sleep(.2)
                else:
                    pytest.fail(f"Workspace link did not open {name} in its renderer: " +
                                str(evaluate(bidi, context, "document.body.innerText"))[-2000:])
                assert evaluate(bidi, context, "location.href") == original_url
        finally:
            if bidi:
                bidi.close()
            if browser:
                _stop(browser)
            _stop(server)
