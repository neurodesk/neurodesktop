import os
import shlex
import signal
import subprocess
import time
import zipfile

import nibabel as nib
import numpy as np
from PIL import Image
import pytest

from native_desktop_driver import Desktop
from vscode_browser_workflow import run_vscode_edit_workflow


def _require_cvmfs():
    if os.environ.get("CVMFS_DISABLE", "false").lower() in {"true", "1"}:
        assert os.environ.get("NEURODESKTOP_REQUIRE_APPLICATIONS") != "1", "Required application checks need CVMFS enabled"
        pytest.skip("CVMFS is explicitly disabled")


def _module_command(module, arguments, *, cwd, timeout=600, env=None):
    _require_cvmfs()
    process = subprocess.Popen(
        ["bash", "-c", "source /opt/neurodesktop/environment_variables.sh && "
         "source /usr/share/lmod/lmod/init/bash && "
         f"module load {shlex.quote(module)} && " + shlex.join(arguments)],
        cwd=cwd, env={**os.environ, **(env or {})},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        raise AssertionError(
            f"{arguments[0]} did not finish within {timeout}s\n{stdout}{stderr}"
        ) from None
    assert process.returncode == 0, stdout + stderr


def test_libreoffice_document_roundtrip_preserves_text(tmp_path):
    expected = "Neurodesktop document acceptance\nSubject 01 measured 42 voxels.\n"
    source = tmp_path / "report.txt"
    source.write_text(expected)
    profile = "-env:UserInstallation=" + (tmp_path / "office-profile").as_uri()
    documents = []
    for extension in ("odt", "docx", "txt"):
        output_dir = tmp_path / extension
        output_dir.mkdir()
        _module_command(
            "libreoffice",
            ["libreoffice", profile, "--headless", "--convert-to", extension,
             "--outdir", str(output_dir), str(source)],
            cwd=tmp_path,
        )
        source = output_dir / f"report.{extension}"
        assert source.is_file(), f"LibreOffice did not save the {extension} document"
        documents.append(source)
    with zipfile.ZipFile(documents[0]) as archive:
        assert archive.read("mimetype") == b"application/vnd.oasis.opendocument.text"
    with zipfile.ZipFile(documents[1]) as archive:
        assert "word/document.xml" in archive.namelist()
    assert source.read_text(encoding="utf-8-sig").replace("\r\n", "\n") == expected


@pytest.fixture
def desktop(tmp_path):
    _require_cvmfs()
    display = Desktop(tmp_path)
    try:
        yield display
    finally:
        display.close()


def test_fsleyes_renders_image_voxels_in_the_selected_colour(tmp_path, desktop):
    volume = np.zeros((32, 32, 32), dtype=np.float32)
    volume[8:24, 8:24, 8:24] = 100
    source = tmp_path / "phantom.nii.gz"
    nib.save(nib.Nifti1Image(volume, np.eye(4)), source)
    rendered = tmp_path / "phantom.png"
    _module_command(
        "fsl", ["fsleyes_unfiltered", "render", "-of", str(rendered), "-sz", "300", "300",
                "-hc", "-hl", "-vl", "16", "16", "16", str(source),
                "-cm", "red", "-dr", "0", "100"],
        cwd=tmp_path, env={"DISPLAY": desktop.name, "LIBGL_ALWAYS_SOFTWARE": "1"},
    )
    assert rendered.is_file(), "FSLeyes did not export its rendered image"
    pixels = np.asarray(Image.open(rendered).convert("RGB"))
    assert pixels.shape == (300, 300, 3)
    red = (pixels[:, :, 0] > 100) & (pixels[:, :, 1] < 10) & (pixels[:, :, 2] < 10)
    assert np.count_nonzero(red) > 1000, "The visible image voxels were not rendered in red"
    assert np.count_nonzero(np.max(pixels, axis=2) < 10) > 1000, "Phantom background was not preserved"


def test_vscode_browser_edits_and_saves_through_authenticated_jupyter(tmp_path):
    run_vscode_edit_workflow(tmp_path)


def test_itksnap_opens_and_exports_the_selected_segmentation(tmp_path, desktop):
    _require_cvmfs()
    affine = np.diag([2.0, 3.0, 4.0, 1.0])
    volume = np.zeros((32, 32, 32), dtype=np.int16)
    volume[8:24, 8:24, 8:24] = 100
    labels = np.zeros(volume.shape, dtype=np.int16)
    labels[12:20, 12:20, 12:20] = 1
    image = tmp_path / "phantom.nii.gz"
    segmentation = tmp_path / "labels.nii.gz"
    output = tmp_path / "exported.nii.gz"
    nib.save(nib.Nifti1Image(volume, affine), image)
    nib.save(nib.Nifti1Image(labels, affine), segmentation)
    arguments = ["itksnap", "--lang", "en", "--geometry", "1024x768+0+0", "-g", str(image),
                 "-s", str(segmentation)]
    with (tmp_path / "itksnap.log").open("w") as log:
        process = subprocess.Popen(
            ["bash", "-c", "source /opt/neurodesktop/environment_variables.sh && "
             "source /usr/share/lmod/lmod/init/bash && module load itksnap && exec "
             + shlex.join(arguments)],
            cwd=tmp_path, env={**os.environ, "DISPLAY": desktop.name,
                "APPTAINERENV_XDG_CONFIG_HOME": str(tmp_path / "itksnap-config")},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            desktop.focus("Layout Preference Reminder", process)
            desktop.chord(0xFF0D)
            desktop.focus("phantom.nii.gz", process)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                assert process.poll() is None, "ITK-SNAP exited before rendering the segmentation"
                desktop.screenshot(tmp_path / "itksnap-opened.png")
                pixels = np.asarray(Image.open(tmp_path / "itksnap-opened.png").convert("RGB"), dtype=int)
                red = ((pixels[:, :, 0] > 200)
                       & (pixels[:, :, 0] > pixels[:, :, 1] + 50)
                       & (pixels[:, :, 0] > pixels[:, :, 2] + 50))
                if np.count_nonzero(red) > 1000:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("ITK-SNAP did not render the selected segmentation")
            desktop.click(125, 10)
            for _ in range(5):
                desktop.chord(0xFF54)
            desktop.chord(0xFF0D)
            desktop.focus("Save Image", process)
            desktop.chord(0xFFE3, ord("a"))
            desktop.text(str(output))
            desktop.chord(0xFF0D)
            deadline = time.monotonic() + 30
            while not output.exists() and time.monotonic() < deadline:
                assert process.poll() is None, "ITK-SNAP exited before saving the segmentation"
                time.sleep(0.1)
            desktop.focus("exported.nii.gz", process, timeout=30)
            desktop.screenshot(tmp_path / "itksnap-saved.png")
            assert output.is_file(), "ITK-SNAP did not save the selected segmentation"
            exported = nib.load(output)
            np.testing.assert_array_equal(exported.get_fdata(), labels)
            np.testing.assert_allclose(exported.affine, affine)
        except Exception as error:
            error.add_note((tmp_path / "itksnap.log").read_text(errors="replace"))
            raise
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
