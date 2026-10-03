"""Build-time contract for Neurodesktop's widget restore workaround."""

import hashlib
import json
import subprocess
import sys

import pytest

from testlib import load_source_module, repo_path


def load_patcher_module():
    return load_source_module(
        "jupyterlab_widgets_patch",
        "/opt/neurodesktop/patch_jupyterlab_widgets.py",
        "config/jupyter/patch_jupyterlab_widgets.py",
    )


def write_labextension_fixture(
    labextension_dir, bundle_source, *, renderer_source
):
    static_dir = labextension_dir / "static"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "32.aaaaaaaaaaaaaaaaaaaa.js"
    bundle.write_text(bundle_source, encoding="utf-8")
    renderer_bundle = static_dir / "160.cccccccccccccccccccc.js"
    renderer_bundle.write_text(renderer_source, encoding="utf-8")
    remote_entry = static_dir / "remoteEntry.bbbbbbbbbbbbbbbb.js"
    remote_entry.write_text(
        'T.u=e=>e+"."+{32:"aaaaaaaaaaaaaaaaaaaa",'
        '160:"cccccccccccccccccccc"}[e]+".js?v="+'
        '{32:"aaaaaaaaaaaaaaaaaaaa",160:"cccccccccccccccccccc"}[e]',
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


def active_bundle_text(tmp_path, package_json, chunk):
    load_path = json.loads(package_json.read_text(encoding="utf-8"))["jupyterlab"][
        "_build"
    ]["load"]
    patched_remote_entry = tmp_path / load_path
    active_bundles = [
        path
        for path in (tmp_path / "static").glob(f"{chunk}.*.js")
        if path.name.split(".")[1]
        in patched_remote_entry.read_text(encoding="utf-8")
    ]
    assert len(active_bundles) == 1
    return active_bundles[0].read_text(encoding="utf-8"), patched_remote_entry


def test_patch_cli_packages_frozen_upstream_assets_under_new_hashes(tmp_path):
    fixture_dir = repo_path("tests/fixtures/widget-manager")
    original_source = (fixture_dir / "32.upstream-excerpts.js").read_text()
    renderer_source = (fixture_dir / "160.upstream-excerpts.js").read_text()
    original_bundle, original_remote_entry, package_json = write_labextension_fixture(
        tmp_path, original_source, renderer_source=renderer_source
    )
    original_files = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    command = [
        sys.executable,
        str(repo_path("config/jupyter/patch_jupyterlab_widgets.py")),
        str(tmp_path),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "workaround applied" in result.stdout
    patched_text, patched_remote_entry = active_bundle_text(tmp_path, package_json, 32)
    renderer_text, _ = active_bundle_text(tmp_path, package_json, 160)
    assert patched_remote_entry != original_remote_entry
    assert "neurodesktop-widget-retry-cache-safe-entry" in patched_remote_entry.read_text()
    for text, markers in (
        (patched_text, (
            "neurodesktop-widget-model-retry",
            "neurodesktop-widget-missing-model-restore-lifecycle",
            "neurodesktop-widget-control-timeout-staged-retry",
            "neurodesktop-widget-control-retry-reconnect",
            "neurodesktop-widget-kernel-connection-reconnect",
        )),
        (renderer_text, (
            "neurodesktop-widget-output-watch",
            "neurodesktop-widget-rerender-after-recovery-failure",
            "neurodesktop-widget-rerender-single-flight",
            "neurodesktop-widget-rerender-on-model-registration",
        )),
    ):
        assert all(marker in text for marker in markers)
    remote_text = patched_remote_entry.read_text()
    for chunk, text in ((32, patched_text), (160, renderer_text)):
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        assert (tmp_path / "static" / f"{chunk}.{digest}.js").read_text() == text
        assert digest in remote_text
    remote_digest = hashlib.sha256(patched_remote_entry.read_bytes()).hexdigest()[:20]
    assert patched_remote_entry.name == f"remoteEntry.{remote_digest}.js"
    for path, data in original_files.items():
        if path != package_json:
            assert path.read_bytes() == data
    assert original_bundle.read_text() == original_source
    packaged_files = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    repeated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert repeated.returncode == 0, repeated.stderr
    assert "workaround already present" in repeated.stdout
    assert {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    } == packaged_files


def test_patch_upgrades_the_existing_missing_model_recovery(tmp_path):
    patcher = load_patcher_module()
    original_bundle, _, package_json = write_labextension_fixture(
        tmp_path,
        patcher.MODEL_RECOVERY_V1_AFTER
        + patcher.CONTROL_TIMEOUT_BEFORE
        + patcher.CONTROL_RETRY_BEFORE
        + patcher.CONNECTION_WAIT_BEFORE,
        renderer_source=(
            patcher.RENDERER_SETUP_BEFORE
            + patcher.RENDERER_RECOVERY_RERENDER_BEFORE
            + patcher.RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE
            + patcher.MODEL_REGISTRATION_RERENDER_BEFORE
        ),
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = active_bundle_text(
        tmp_path,
        package_json,
        32,
    )
    assert patcher.MODEL_RETRY_MARKER in patched_text
    assert patcher.MODEL_RECOVERY_MARKER in patched_text
    assert patcher.MODEL_RECOVERY_COOLDOWN_MARKER in patched_text
    assert patcher.MODEL_RECOVERY_LIFECYCLE_MARKER in patched_text
    assert patcher.CONTROL_TIMEOUT_MARKER in patched_text
    assert patcher.CONTROL_RETRY_MARKER in patched_text
    assert patcher.CONNECTION_WAIT_MARKER in patched_text
    assert not patcher.patch_labextension(tmp_path)


def test_patch_upgrades_recovery_that_bypasses_restore_lifecycle(tmp_path):
    patcher = load_patcher_module()
    _, _, package_json = write_labextension_fixture(
        tmp_path,
        patcher.MODEL_RECOVERY_V2_AFTER
        + patcher.CONTROL_TIMEOUT_AFTER
        + patcher.CONTROL_RETRY_AFTER
        + patcher.CONNECTION_WAIT_AFTER,
        renderer_source=(
            patcher.RENDERER_OUTPUT_WATCH_AFTER
            + patcher.RENDERER_RECOVERY_RERENDER_AFTER
            + patcher.RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE
            + patcher.MODEL_REGISTRATION_RERENDER_BEFORE
        ),
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = active_bundle_text(tmp_path, package_json, 32)
    assert patcher.MODEL_RECOVERY_LIFECYCLE_MARKER in patched_text
    assert "this.restoreWidgets(this.context&&this.context.model" in patched_text
    assert "neurodeskRecovery=this._loadFromKernel()" not in patched_text
    renderer_text, _ = active_bundle_text(tmp_path, package_json, 160)
    assert patcher.RENDERER_RERENDER_SINGLE_FLIGHT_MARKER in renderer_text
    assert patcher.MODEL_REGISTRATION_RERENDER_MARKER in renderer_text
    assert not patcher.patch_labextension(tmp_path)


def test_patch_upgrades_the_existing_wait_workaround(tmp_path):
    patcher = load_patcher_module()
    original_bundle, _, package_json = write_labextension_fixture(
        tmp_path,
        patcher.MODEL_RETRY_AFTER
        + patcher.CONTROL_TIMEOUT_V1_AFTER
        + patcher.CONTROL_RETRY_V1_AFTER
        + patcher.CONNECTION_WAIT_BEFORE,
        renderer_source=(
            patcher.RENDERER_SETUP_BEFORE
            + patcher.RENDERER_RECOVERY_RERENDER_BEFORE
            + patcher.RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE
            + patcher.MODEL_REGISTRATION_RERENDER_BEFORE
        ),
    )
    (tmp_path / "static" / "32.dddddddddddddddddddd.js").write_text(
        patcher.MODEL_RETRY_BEFORE
        + patcher.CONTROL_TIMEOUT_BEFORE
        + patcher.CONTROL_RETRY_BEFORE
        + patcher.CONNECTION_WAIT_BEFORE,
        encoding="utf-8",
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = active_bundle_text(
        tmp_path,
        package_json,
        32,
    )
    assert patcher.MODEL_RETRY_MARKER in patched_text
    assert patcher.MODEL_RECOVERY_MARKER in patched_text
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
        renderer_source=(
            patcher.RENDERER_OUTPUT_WATCH_AFTER
            + patcher.RENDERER_RECOVERY_RERENDER_BEFORE
            + patcher.RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE
            + patcher.MODEL_REGISTRATION_RERENDER_BEFORE
        ),
    )

    assert patcher.patch_labextension(tmp_path)

    patched_text, _ = active_bundle_text(
        tmp_path,
        package_json,
        32,
    )
    assert patcher.MODEL_RECOVERY_MARKER in patched_text
    assert patcher.CONTROL_RETRY_MARKER in patched_text
    assert "neurodeskRetryKernel.reconnect()" in patched_text
    assert patcher.CONNECTION_WAIT_MARKER in patched_text
    assert "neurodeskKernel.reconnect().then(()=>!0)" in patched_text
    renderer_text, _ = active_bundle_text(tmp_path, package_json, 160)
    assert patcher.RENDERER_OUTPUT_WATCH_MARKER in renderer_text
    assert patcher.RENDERER_RECOVERY_RERENDER_MARKER in renderer_text
    assert not patcher.patch_labextension(tmp_path)


def test_patch_refuses_widget_manager_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    bundle, _, _ = write_labextension_fixture(
        tmp_path,
        "upstream changed",
        renderer_source=(
            patcher.RENDERER_SETUP_BEFORE
            + patcher.RENDERER_RECOVERY_RERENDER_BEFORE
            + patcher.RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE
            + patcher.MODEL_REGISTRATION_RERENDER_BEFORE
        ),
    )

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
