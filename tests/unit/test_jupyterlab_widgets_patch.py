"""Build-time contract for Neurodesktop's widget restore workaround."""

import json

import pytest

from testlib import load_source_module, repo_path


def load_patcher_module():
    return load_source_module(
        "jupyterlab_widgets_patch",
        "/opt/neurodesktop/patch_jupyterlab_widgets.py",
        "config/jupyter/patch_jupyterlab_widgets.py",
    )


def write_labextension_fixture(labextension_dir, bundle_source):
    static_dir = labextension_dir / "static"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "32.aaaaaaaaaaaaaaaaaaaa.js"
    bundle.write_text(bundle_source, encoding="utf-8")
    remote_entry = static_dir / "remoteEntry.bbbbbbbbbbbbbbbb.js"
    remote_entry.write_text(
        'T.u=e=>e+"."+{32:"aaaaaaaaaaaaaaaaaaaa"}[e]+".js?v="+'
        '{32:"aaaaaaaaaaaaaaaaaaaa"}[e]',
        encoding="utf-8",
    )
    package_json = labextension_dir / "package.json"
    package_json.write_text(
        json.dumps(
            {
                "name": "@jupyter-widgets/jupyterlab-manager",
                "version": "5.0.16",
                "jupyterlab": {
                    "_build": {
                        "load": "static/remoteEntry.bbbbbbbbbbbbbbbb.js"
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return bundle, remote_entry, package_json


def patched_bundle_text(tmp_path, original_bundle, package_json):
    load_path = json.loads(package_json.read_text(encoding="utf-8"))["jupyterlab"][
        "_build"
    ]["load"]
    patched_remote_entry = tmp_path / load_path
    patched_bundles = [
        path
        for path in (tmp_path / "static").glob("32.*.js")
        if path != original_bundle
        and path.name.split(".")[1]
        in patched_remote_entry.read_text(encoding="utf-8")
    ]
    assert len(patched_bundles) == 1
    return patched_bundles[0].read_text(encoding="utf-8"), patched_remote_entry


def test_patch_waits_for_the_kernel_and_retries_a_lost_bulk_request(tmp_path):
    patcher = load_patcher_module()
    original_source = (
        patcher.MODEL_RETRY_BEFORE
        + patcher.CONTROL_TIMEOUT_BEFORE
        + patcher.CONTROL_RETRY_BEFORE
        + patcher.CONNECTION_WAIT_BEFORE
    )
    original_bundle, original_remote_entry, package_json = write_labextension_fixture(
        tmp_path,
        original_source,
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, patched_remote_entry = patched_bundle_text(
        tmp_path,
        original_bundle,
        package_json,
    )
    assert patched_remote_entry != original_remote_entry
    assert patcher.CACHE_SAFE_MARKER in patched_remote_entry.read_text(
        encoding="utf-8"
    )
    assert patcher.MODEL_RETRY_MARKER in patched_text
    assert "Date.now()-o<1e4" in patched_text
    assert patcher.CONTROL_TIMEOUT_MARKER in patched_text
    assert "this.__neurodesktopControlRetry?3e4:1e4" in patched_text
    assert patcher.CONTROL_RETRY_MARKER in patched_text
    assert "neurodeskRetryKernel.reconnect()" in patched_text
    assert "return await this._loadFromKernel()" in patched_text
    assert "neurodeskRetries<2" in patched_text
    assert patcher.CONNECTION_WAIT_MARKER in patched_text
    assert "if(!this.__neurodesktopControlRetry)" in patched_text
    assert '"connected"!==neurodeskKernel.connectionStatus' in patched_text
    assert "neurodeskKernel.requestKernelInfo()" in patched_text
    assert "neurodeskKernel.reconnect().then(()=>!0)" in patched_text
    assert "__neurodesktopKernelProbeStatus" in patched_text
    assert 'readiness probe")),3e3)' in patched_text
    assert original_bundle.read_text(encoding="utf-8") == original_source
    assert not patcher.patch_labextension(tmp_path)


def test_patch_upgrades_the_existing_model_only_workaround(tmp_path):
    patcher = load_patcher_module()
    original_bundle, _, package_json = write_labextension_fixture(
        tmp_path,
        patcher.MODEL_RETRY_AFTER
        + patcher.CONTROL_TIMEOUT_BEFORE
        + patcher.CONTROL_RETRY_BEFORE
        + patcher.CONNECTION_WAIT_BEFORE,
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = patched_bundle_text(
        tmp_path,
        original_bundle,
        package_json,
    )
    assert patcher.MODEL_RETRY_MARKER in patched_text
    assert patcher.CONTROL_TIMEOUT_MARKER in patched_text
    assert patcher.CONTROL_RETRY_MARKER in patched_text
    assert patcher.CONNECTION_WAIT_MARKER in patched_text
    assert not patcher.patch_labextension(tmp_path)


def test_patch_upgrades_the_existing_wait_workaround(tmp_path):
    patcher = load_patcher_module()
    original_bundle, _, package_json = write_labextension_fixture(
        tmp_path,
        patcher.MODEL_RETRY_AFTER
        + patcher.CONTROL_TIMEOUT_V1_AFTER
        + patcher.CONTROL_RETRY_V1_AFTER
        + patcher.CONNECTION_WAIT_BEFORE,
    )
    (tmp_path / "static" / "32.dddddddddddddddddddd.js").write_text(
        patcher.MODEL_RETRY_BEFORE
        + patcher.CONTROL_TIMEOUT_BEFORE
        + patcher.CONTROL_RETRY_BEFORE
        + patcher.CONNECTION_WAIT_BEFORE,
        encoding="utf-8",
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = patched_bundle_text(
        tmp_path,
        original_bundle,
        package_json,
    )
    assert patcher.MODEL_RETRY_MARKER in patched_text
    assert patcher.CONTROL_TIMEOUT_MARKER in patched_text
    assert patcher.CONTROL_RETRY_MARKER in patched_text
    assert patcher.CONNECTION_WAIT_MARKER in patched_text
    assert not patcher.patch_labextension(tmp_path)


def test_patch_upgrades_the_existing_two_retry_workaround(tmp_path):
    patcher = load_patcher_module()
    original_bundle, _, package_json = write_labextension_fixture(
        tmp_path,
        patcher.MODEL_RETRY_AFTER
        + patcher.CONTROL_TIMEOUT_AFTER
        + patcher.CONTROL_RETRY_V2_AFTER
        + patcher.CONNECTION_WAIT_V1_AFTER,
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = patched_bundle_text(
        tmp_path,
        original_bundle,
        package_json,
    )
    assert patcher.CONTROL_RETRY_MARKER in patched_text
    assert "neurodeskRetryKernel.reconnect()" in patched_text
    assert patcher.CONNECTION_WAIT_MARKER in patched_text
    assert "neurodeskKernel.reconnect().then(()=>!0)" in patched_text
    assert not patcher.patch_labextension(tmp_path)


def test_patch_refuses_widget_manager_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    bundle, _, _ = write_labextension_fixture(tmp_path, "upstream changed")

    with pytest.raises(ValueError, match="model retry anchor"):
        patcher.patch_labextension(tmp_path)

    assert bundle.read_text(encoding="utf-8") == "upstream changed"


def test_dockerfile_applies_widget_patch_after_package_install():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_pin = dockerfile.index("jupyterlab_widgets==3.0.17")
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyterlab_widgets.py"
    )
    assert package_pin < patch_run


def test_dockerfile_pins_ipykernel_before_experimental_subshells():
    """Widget comms stay on the stable main-shell path."""
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert "ipykernel==6.31.0" in dockerfile
