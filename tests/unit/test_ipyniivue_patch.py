"""Build-time contract for Neurodesktop's ipyniivue frontend workaround."""

import hashlib
import re

import pytest

from testlib import load_source_module, repo_path


IPYNIIVUE_SYNC_LOOP = (
    'function S0(A,I){BC!==void 0&&(clearInterval(BC),BC=void 0);'
    'let B=I.get("scene"),C=!1;BC=setInterval(async()=>{if(!C)return;'
    'let E=I.get("this_model_id");if(!E)return;let t;try{'
    't=await I.widget_manager.get_model(E)}catch{return}let i={'
    'renderAzimuth:A.scene.renderAzimuth,'
    'renderElevation:A.scene.renderElevation,'
    'volScaleMultiplier:A.scene.volScaleMultiplier,'
    'crosshairPos:[...A.scene.crosshairPos],'
    'clipPlanes:A.scene.clipPlanes.map(a=>[...a]),'
    'clipPlaneDepthAziElevs:A.scene.clipPlaneDepthAziElevs.map(a=>[...a]),'
    'pan2Dxyzmm:[...A.scene.pan2Dxyzmm],gamma:A.scene.gamma||1},'
    'o=D2(B,i);Object.keys(o).length>0&&(e2(t,{scene:o}),B=i)},30);'
    'let Q=A.sync;A.sync=new Proxy(Q,{apply:(E,t,i)=>{if('
    'Reflect.apply(E,t,i),!A.gl){C=!1;return}if(!A.gl.canvas.matches('
    '":focus")){C=!1;return}C=!0}})}'
)


IPYNIIVUE_TAIL = (
    'var vA,BC;async function BB(A,I){return A+I}'
    f"{IPYNIIVUE_SYNC_LOOP}"
    'var Ih={async initialize({model:A}){let I=new $B;if(!vA){'
    'console.log("Creating new Niivue instance");vA=new t2(A)}return()=>{'
    'I.disposeAll(),A.off("change:volumes"),clearInterval(BC)}},'
    'async render({model:A,el:I}){console.log("drawing first render");'
    'return()=>{vA.canvas?.remove()}}};export{Ih as default};'
)


def load_patcher_module():
    return load_source_module(
        "ipyniivue_patch",
        "/opt/neurodesktop/patch_ipyniivue.py",
        "config/jupyter/patch_ipyniivue.py",
    )


def write_ipyniivue_fixture(tmp_path, source=IPYNIIVUE_TAIL):
    package_dir = tmp_path / "site-packages/ipyniivue"
    widget_path = package_dir / "static/widget.js"
    widget_path.parent.mkdir(parents=True)
    widget_path.write_text(source, encoding="utf-8")
    lab_static_dir = tmp_path / "share/jupyter/lab/static"
    lab_static_dir.mkdir(parents=True)
    return package_dir, widget_path, lab_static_dir


def test_patch_shares_bundle_but_creates_one_definition_per_model(tmp_path):
    patcher = load_patcher_module()
    package_dir, widget_path, lab_static_dir = write_ipyniivue_fixture(tmp_path)

    assert patcher.patch_ipyniivue(package_dir, lab_static_dir)

    bootstrap = widget_path.read_text(encoding="utf-8")
    assert len(bootstrap) < 2_000
    assert "jupyter-config-data" in bootstrap
    assert "baseUrl" in bootstrap
    assert "createWidgetDefinition()" in bootstrap

    asset_names = re.findall(
        r"neurodesktop-ipyniivue\.[0-9a-f]{20}\.js", bootstrap
    )
    assert len(asset_names) == 1
    shared_bundle = lab_static_dir / asset_names[0]
    shared_source = shared_bundle.read_text(encoding="utf-8")
    assert hashlib.sha256(shared_source.encode("utf-8")).hexdigest()[:20] in (
        shared_bundle.name
    )
    assert "function createWidgetDefinition(){let vA;" in shared_source
    assert "export{createWidgetDefinition};" in shared_source
    assert "export{Ih as default};" not in shared_source
    assert patcher.MODEL_CLEANUP_MARKER in shared_source
    assert 'getExtension("WEBGL_lose_context")?.loseContext()' in shared_source
    assert "vA=void 0" in shared_source
    assert patcher.SCENE_SYNC_MARKER in shared_source
    assert "setInterval(" not in shared_source
    assert "let D=Reflect.apply(o,a,e)" in shared_source
    assert 'A.gl.canvas.matches(":focus")&&C(),D' in shared_source
    assert not patcher.patch_ipyniivue(package_dir, lab_static_dir)


def test_patch_refuses_frontend_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    _, widget_path, lab_static_dir = write_ipyniivue_fixture(
        tmp_path, "upstream changed"
    )

    with pytest.raises(ValueError, match="anchor"):
        patcher.patch_ipyniivue(widget_path.parent.parent, lab_static_dir)

    assert widget_path.read_text(encoding="utf-8") == "upstream changed"
    assert not list(lab_static_dir.iterdir())


def test_dockerfile_pins_and_patches_ipyniivue_in_its_install_layer():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG IPYNIIVUE_VERSION=\"2.4.4\"" in dockerfile
    assert "ipyniivue==${IPYNIIVUE_VERSION}" in dockerfile
    assert "source=config/jupyter/patch_ipyniivue.py," in dockerfile
    assert (
        "/opt/conda/bin/python /opt/neurodesktop/patch_ipyniivue.py"
        in dockerfile
    )

    install_run = dockerfile.index(
        "RUN --mount=type=bind,source=config/jupyter/patch_ipyniivue.py"
    )
    next_run = dockerfile.index("\nRUN ", install_run + 1)
    package_pin = dockerfile.index("ipyniivue==${IPYNIIVUE_VERSION}", install_run)
    patch_command = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_ipyniivue.py",
        install_run,
    )
    assert install_run < package_pin < patch_command < next_run
