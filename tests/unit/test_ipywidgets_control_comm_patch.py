"""Build-time contract for multi-client ipywidgets state restoration."""

import pytest

from testlib import load_source_module, repo_path


WIDGET_SOURCE = '''class Widget:
    _control_comm = None

    @classmethod
    def handle_control_comm_opened(cls, comm, msg):
        version = msg.get('metadata', {}).get('version', '')
        if version.split('.')[0] != '1':
            raise ValueError('Incompatible widget control protocol version')

        cls._control_comm = comm
        cls._control_comm.on_msg(cls._handle_control_comm_msg)

    @classmethod
    def _handle_control_comm_msg(cls, msg):
        # This shouldn't happen unless someone calls this method manually
        if cls._control_comm is None:
            raise RuntimeError('Control comm has not been properly opened')

        data = msg['content']['data']
        method = data['method']

        if method == 'request_states':
            full_state = {}
            buffer_paths = []
            buffers = []
            cls._control_comm.send(dict(
                method='update_states',
                states=full_state,
                buffer_paths=buffer_paths
            ), buffers=buffers)
'''


def load_patcher_module():
    return load_source_module(
        "ipywidgets_control_comm_patch",
        "/opt/neurodesktop/patch_ipywidgets_control_comm.py",
        "config/jupyter/patch_ipywidgets_control_comm.py",
    )


def write_upstream_fixture(package_dir, source=WIDGET_SOURCE):
    widgets_dir = package_dir / "widgets"
    widgets_dir.mkdir()
    (widgets_dir / "widget.py").write_text(source, encoding="utf-8")


class Comm:
    def __init__(self):
        self.callback = None
        self.sent = []

    def on_msg(self, callback):
        self.callback = callback

    def send(self, data, buffers=None):
        self.sent.append(data)


def test_patch_routes_each_state_reply_to_its_requesting_comm(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)

    assert patcher.patch_package(tmp_path)
    patched = (tmp_path / "widgets" / "widget.py").read_text(encoding="utf-8")
    assert patcher.MARKER in patched
    assert "control_comm.send(dict(" in patched

    namespace = {}
    exec(compile(patched, "widget.py", "exec"), namespace)
    widget = namespace["Widget"]
    first = Comm()
    second = Comm()
    opened = {"metadata": {"version": "1.0.0"}}
    request = {"content": {"data": {"method": "request_states"}}}

    widget.handle_control_comm_opened(first, opened)
    widget.handle_control_comm_opened(second, opened)
    first.callback(request)
    second.callback(request)

    assert [reply["method"] for reply in first.sent] == ["update_states"]
    assert [reply["method"] for reply in second.sent] == ["update_states"]

    widget._handle_control_comm_msg(request)
    assert [reply["method"] for reply in second.sent] == [
        "update_states",
        "update_states",
    ]
    assert not patcher.patch_package(tmp_path)


def test_patch_refuses_control_comm_anchor_drift(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path, "upstream changed\n")

    with pytest.raises(ValueError, match="anchor"):
        patcher.patch_package(tmp_path)


def test_dockerfile_applies_control_comm_patch_after_ipywidgets_pin():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_pin = dockerfile.index("ipywidgets==8.1.9")
    patch_install = dockerfile.index(
        "/opt/neurodesktop/patch_ipywidgets_control_comm.py"
    )
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_ipywidgets_control_comm.py"
    )
    assert package_pin < patch_install < patch_run
