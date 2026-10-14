import json
import os
import shutil
import subprocess
import time

from test_rise_slides_image import _BidiSession, _unused_port, _wait_for_server, _stop
from test_workspace_link_routing_image import evaluate


def wait_for(bidi, context, expression):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if evaluate(bidi, context, expression):
            return
        time.sleep(0.2)
    raise AssertionError(
        str(evaluate(bidi, context, "document.body.innerText"))[-4000:]
    )


def click(bidi, context, expression):
    point = json.loads(
        evaluate(
            bidi,
            context,
            """JSON.stringify((() => {
        const node = """
            + expression
            + """;
        node.scrollIntoView({block:'center'});
        const r = node.getBoundingClientRect();
        return {x:Math.round(r.left+Math.min(r.width/2,20)),y:Math.round(r.top+r.height/2)};
    })())""",
        )
    )
    assert evaluate(
        bidi,
        context,
        f"Boolean(({expression}).contains(document.elementFromPoint({point['x']},{point['y']})))",
    ), point
    actions = [{"type": "pointerMove", "duration": 0, "origin": "viewport", **point}]
    actions += [
        {"type": "pointerDown", "button": 0},
        {"type": "pointerUp", "button": 0},
        {"type": "pause", "duration": 100},
    ]
    bidi.request(
        "input.performActions",
        {
            "context": context,
            "actions": [
                {
                    "type": "pointer",
                    "id": "astra-user",
                    "parameters": {"pointerType": "mouse"},
                    "actions": actions,
                }
            ],
        },
    )


def test_file_browser_astra_document_refreshes_saved_spec_and_run(tmp_path):
    shutil.copytree("/opt/neurodesktop/examples/astra-bet", tmp_path / "analysis")
    server_port, browser_port = _unused_port(), _unused_port()
    prefix = f"http://127.0.0.1:{server_port}/user/astra-test/"
    token = "astra-document-test"
    environment = {**os.environ, "HOME": str(tmp_path)}
    config = tmp_path / "jupyter_server_config.py"
    config.write_text(
        'c = get_config()\nc.SlurmCommandPaths.squeue_path = "/usr/bin/true"\n'
    )
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    with (tmp_path / "jupyter.log").open("w") as log, (tmp_path / "firefox.log").open(
        "w"
    ) as browser_log:
        server = subprocess.Popen(
            [
                "jupyter",
                "lab",
                "--no-browser",
                f"--config={config}",
                "--LabApp.expose_app_in_browser=True",
                f"--ServerApp.port={server_port}",
                "--ServerApp.port_retries=0",
                "--ServerApp.base_url=/user/astra-test/",
                f"--ServerApp.root_dir={tmp_path}",
                f"--FileContentsManager.preferred_dir={tmp_path}",
                f"--IdentityProvider.token={token}",
                '--ServerApp.jpserver_extensions={"jupyterlab":True,"neurodesk_t3_code":False}',
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        browser, bidi = None, None
        try:
            _wait_for_server(
                prefix + "api/status?token=" + token, server, tmp_path / "jupyter.log"
            )
            browser = subprocess.Popen(
                [
                    "/usr/bin/firefox",
                    "--headless",
                    "--profile",
                    str(profile),
                    f"--remote-debugging-port={browser_port}",
                    "about:blank",
                ],
                env=environment,
                stdout=browser_log,
                stderr=subprocess.STDOUT,
            )
            bidi = _BidiSession(f"ws://127.0.0.1:{browser_port}/session", browser)
            bidi.request("session.new", {"capabilities": {}})
            context = bidi.request("browsingContext.create", {"type": "tab"})["context"]
            bidi.request(
                "browsingContext.navigate",
                {
                    "context": context,
                    "url": prefix + "lab?token=" + token,
                    "wait": "interactive",
                },
            )
            wait_for(bidi, context, "Boolean(window.jupyterapp?.started)")
            evaluate(bidi, context, "window.jupyterapp.started.then(()=>true)")
            evaluate(bidi, context, "window.jupyterapp.restored.then(()=>true)")
            evaluate(
                bidi,
                context,
                "window.jupyterapp.commands.execute('filebrowser:activate').then(()=>true)",
            )
            evaluate(
                bidi,
                context,
                "window.jupyterapp.activatePlugin('neurodesk-launcher:astra-viewer').then(()=>true)",
            )
            row = "Array.from(document.querySelectorAll('.jp-DirListing-item')).find(n=>n.querySelector('.jp-DirListing-itemText')?.textContent==='analysis' && n.checkVisibility())"
            wait_for(bidi, context, "Boolean(" + row + ")")
            click(bidi, context, row)
            bidi.request(
                "input.performActions",
                {
                    "context": context,
                    "actions": [
                        {
                            "type": "key",
                            "id": "open-file",
                            "actions": [
                                {"type": "keyDown", "value": "\ue007"},
                                {"type": "keyUp", "value": "\ue007"},
                            ],
                        }
                    ],
                },
            )
            row = "Array.from(document.querySelectorAll('.jp-DirListing-item')).find(n=>n.querySelector('.jp-DirListing-itemText')?.textContent==='astra.yaml' && n.checkVisibility())"
            wait_for(bidi, context, "Boolean(" + row + ")")
            click(bidi, context, row)
            bidi.request(
                "input.performActions",
                {
                    "context": context,
                    "actions": [
                        {
                            "type": "key",
                            "id": "open-file",
                            "actions": [
                                {"type": "keyDown", "value": "\ue007"},
                                {"type": "keyUp", "value": "\ue007"},
                            ],
                        }
                    ],
                },
            )
            wait_for(
                bidi,
                context,
                "Boolean(document.querySelector('.nd-astra-document svg'))",
            )
            assert evaluate(
                bidi,
                context,
                "document.querySelector('.nd-astra-document').textContent.includes('BET threshold sensitivity')",
            )
            assert (
                evaluate(
                    bidi, context, "document.querySelector('.astra-trust').textContent"
                )
                == "Not executed"
            )
            picker = "document.querySelector('.nd-astra-universe-bar select')"
            evaluate(bidi, context, picker + ".focus(); true")
            bidi.request(
                "input.performActions",
                {
                    "context": context,
                    "actions": [
                        {
                            "type": "key",
                            "id": "astra-key",
                            "actions": [
                                {"type": "keyDown", "value": "\ue00c"},
                                {"type": "keyUp", "value": "\ue00c"},
                                {"type": "keyDown", "value": "\ue010"},
                                {"type": "keyUp", "value": "\ue010"},
                                {"type": "keyDown", "value": "\ue004"},
                                {"type": "keyUp", "value": "\ue004"},
                            ],
                        }
                    ],
                },
            )
            wait_for(
                bidi,
                context,
                "document.querySelector('.astra-universe')?.textContent==='universe: bet-f-0-5'",
            )
            click(
                bidi,
                context,
                "document.querySelector('.nd-astra-document [data-mode=\"evidence\"]')",
            )
            manifest = {
                "runtime": "slurm",
                "outputs": [
                    {
                        "output_id": "boundary_qc",
                        "universe_id": "bet-f-0-5",
                        "status": "ok",
                    }
                ],
            }
            save = (
                """(async()=>{
                const contents=window.jupyterapp.serviceManager.contents;
                const spec=await contents.get('analysis/astra.yaml',{content:true});
                spec.content=spec.content.replace('BET threshold sensitivity','Refreshed BET analysis');
                await contents.save('analysis/astra.yaml',spec);
                await contents.save('analysis/status.json',{type:'file',format:'text',content:"""
                + json.dumps(json.dumps(manifest))
                + """});
                return true;
            })()"""
            )
            evaluate(bidi, context, save)
            click(bidi, context, "document.querySelector('.nd-astra-refresh')")
            wait_for(
                bidi,
                context,
                """(() => {
                    const documentView = document.querySelector('.nd-astra-document');
                    return documentView?.textContent.includes('Refreshed BET analysis')
                        && documentView.querySelector('.astra-trust')?.textContent === 'Executed, unverified'
                        && documentView.querySelector('.nd-astra-universe-bar select')?.value === 'analysis/universes/bet-f-0-5.yaml'
                        && documentView.querySelector('[data-mode="evidence"]')?.classList.contains('active')
                        && Boolean(documentView.querySelector('svg .astra-node'));
                })()""",
            )
        finally:
            if bidi:
                bidi.close()
            if browser:
                _stop(browser)
            _stop(server)
