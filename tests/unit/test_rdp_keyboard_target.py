import ctypes as c
import importlib.util
import os
import select
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from testlib import repo_path


@pytest.fixture
def target_module(monkeypatch):
    monkeypatch.syspath_prepend(str(repo_path('tests/container')))
    spec = importlib.util.spec_from_file_location('rdp_keyboard_target', repo_path('tests/container/rdp_keyboard_target.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def xserver(target_module, tmp_path):
    reader, writer = os.pipe()
    with (tmp_path / 'xvfb.log').open('w') as log:
        server = subprocess.Popen(['Xvfb', '-displayfd', str(writer), '-nolisten', 'tcp'],
                                  pass_fds=(writer,), stdout=log, stderr=subprocess.STDOUT)
        os.close(writer)
        try:
            assert select.select([reader], [], [], 5)[0], 'Xvfb did not start'
            with os.fdopen(reader) as announcement:
                number = announcement.readline().strip()
            reader = None
            assert number.isdigit(), (tmp_path / 'xvfb.log').read_text()
            display = ':' + number
            x = c.CDLL(target_module.ctypes.util.find_library('X11'))
            x.XOpenDisplay.argtypes, x.XOpenDisplay.restype = [c.c_char_p], c.c_void_p
            x.XDefaultRootWindow.argtypes, x.XDefaultRootWindow.restype = [c.c_void_p], c.c_ulong
            x.XCreateSimpleWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint,
                                             c.c_uint, c.c_uint, c.c_ulong, c.c_ulong]
            x.XCreateSimpleWindow.restype = c.c_ulong
            x.XInternAtom.argtypes, x.XInternAtom.restype = [c.c_void_p, c.c_char_p, c.c_int], c.c_ulong
            x.XChangeProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_ulong,
                                         c.c_int, c.c_int, c.c_void_p, c.c_int]
            x.XStoreName.argtypes = [c.c_void_p, c.c_ulong, c.c_char_p]
            x.XSetInputFocus.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_ulong]
            for name in ('XMapWindow', 'XUnmapWindow'):
                getattr(x, name).argtypes = [c.c_void_p, c.c_ulong]
            x.XSync.argtypes = [c.c_void_p, c.c_int]
            x.XCloseDisplay.argtypes = [c.c_void_p]
            connection = x.XOpenDisplay(display.encode())
            assert connection
            root = x.XDefaultRootWindow(connection)

            def window(parent, name):
                result = x.XCreateSimpleWindow(connection, parent, 0, 0, 100, 100, 0, 0, 0)
                x.XStoreName(connection, result, name.encode())
                return result

            def prop(window, name, values):
                x.XChangeProperty(connection, window, x.XInternAtom(connection, name.encode(), 0),
                                  33, 32, 0, (c.c_ulong * len(values))(*values), len(values))

            def observe():
                x.XSync(connection, 0)
                return target_module._snapshot(display)

            yield SimpleNamespace(x=x, connection=connection, root=root, window=window,
                                  prop=prop, observe=observe)
            x.XCloseDisplay(connection)
        finally:
            if reader is not None:
                os.close(reader)
            server.terminate()
            server.wait(timeout=5)


def test_real_x11_requires_registered_wm_and_mapped_panel(xserver):
    s = xserver
    wm, panel = s.window(s.root, 'WM'), s.window(s.root, 'panel')
    s.prop(s.root, '_NET_CLIENT_LIST', [panel])
    s.x.XMapWindow(s.connection, panel)
    assert not s.observe()['hotkey_ready']
    s.prop(s.root, '_NET_SUPPORTING_WM_CHECK', [wm])
    assert not s.observe()['hotkey_ready']
    s.prop(wm, '_NET_SUPPORTING_WM_CHECK', [wm])
    assert s.observe()['hotkey_ready']
    s.x.XUnmapWindow(s.connection, panel)
    assert not s.observe()['hotkey_ready']


def test_real_x11_requires_run_mapped_active_and_child_focused(xserver):
    s = xserver
    wm, run, other = [s.window(s.root, title) for title in ('WM', 'Run', 'other')]
    entry = s.window(run, 'entry')
    s.prop(s.root, '_NET_SUPPORTING_WM_CHECK', [wm])
    s.prop(wm, '_NET_SUPPORTING_WM_CHECK', [wm])
    s.prop(s.root, '_NET_CLIENT_LIST', [run, other])
    s.prop(s.root, '_NET_ACTIVE_WINDOW', [run])
    assert not s.observe()['run_focused']
    for window in (run, entry, other):
        s.x.XMapWindow(s.connection, window)
    s.x.XSetInputFocus(s.connection, other, 1, 0)
    assert not s.observe()['run_focused']
    s.x.XSetInputFocus(s.connection, run, 1, 0)
    assert not s.observe()['run_focused']
    s.x.XSetInputFocus(s.connection, entry, 1, 0)
    assert s.observe()['run_focused']
    s.prop(s.root, '_NET_ACTIVE_WINDOW', [other])
    assert not s.observe()['run_focused']


@pytest.fixture
def waiting(target_module, monkeypatch):
    module = target_module
    monkeypatch.setattr(module, '_xrdp_sessions', lambda uid: {})
    target = module.RdpKeyboardTarget(1000)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))

    class Tunnel:
        timeout = 2
        failure = None

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

        def recv(self):
            clock.now += self.timeout
            if self.failure is not None:
                if isinstance(self.failure, Exception):
                    raise self.failure
                return self.failure
            raise module.websocket.WebSocketTimeoutException()

    return target, Tunnel(), clock


def test_wait_observes_delayed_keyboard_target(waiting, monkeypatch):
    target, tunnel, clock = waiting
    monkeypatch.setattr(target, 'snapshot', lambda timeout: {'run_focused': clock.now >= 0.5})
    assert target.wait(tunnel, run_dialog=True, timeout_seconds=1)['run_focused']
    assert 0.5 <= clock.now < 1
    assert tunnel.timeout == 2


def test_wait_reports_observed_state_at_deadline(waiting, monkeypatch):
    target, tunnel, clock = waiting
    monkeypatch.setattr(target, 'snapshot', lambda timeout: {'run_focused': False, 'focus': 17})
    with pytest.raises(AssertionError, match="focused Run dialog.*focus.*17"):
        target.wait(tunnel, run_dialog=True, timeout_seconds=1)
    assert clock.now == pytest.approx(1)
    assert tunnel.timeout == 2


def test_wait_rejects_readiness_observed_after_deadline(waiting, monkeypatch):
    target, tunnel, clock = waiting

    def late_snapshot(timeout):
        clock.now += 2
        return {'hotkey_ready': True}

    monkeypatch.setattr(target, 'snapshot', late_snapshot)
    with pytest.raises(AssertionError, match='No desktop hotkey target within 1s'):
        target.wait(tunnel, timeout_seconds=1)


@pytest.mark.parametrize('failure', ['', '5.error,6.denied,3.768;', ConnectionResetError()])
def test_wait_rejects_broken_transport_even_if_target_ready(waiting, monkeypatch, failure):
    target, tunnel, _ = waiting
    tunnel.failure = failure
    monkeypatch.setattr(target, 'snapshot', lambda timeout: {'hotkey_ready': True})
    with pytest.raises(AssertionError, match='Guacamole'):
        target.wait(tunnel)
    assert tunnel.timeout == 2


def test_does_not_attach_to_a_previous_session(target_module, monkeypatch):
    monkeypatch.setattr(target_module, '_xrdp_sessions', lambda uid: {42: (':19', '/home/user/.Xauthority')})
    target = target_module.RdpKeyboardTarget(1000)
    assert target.snapshot(1) == {'waiting': 'new notebook-user XRDP session'}


def test_uses_new_session_authority_and_rejects_session_loss(target_module, monkeypatch):
    sessions = {42: (':19', '/home/user/.Xauthority')}
    monkeypatch.setattr(target_module, '_xrdp_sessions', lambda uid: dict(sessions))
    target = target_module.RdpKeyboardTarget(1000)
    sessions[73] = (':27', '/home/user/custom-authority')

    def probe(args, **kwargs):
        assert args == [sys.executable, target_module.__file__, ':27']
        assert kwargs['env']['XAUTHORITY'] == '/home/user/custom-authority'
        assert kwargs['user'] == 1000
        return SimpleNamespace(returncode=0, stdout='{"hotkey_ready":true}', stderr='')

    monkeypatch.setattr(target_module.subprocess, 'run', probe)
    assert target.snapshot(1)['display'] == ':27'
    del sessions[73]
    with pytest.raises(AssertionError, match='exited or changed'):
        target.snapshot(1)


def test_discovers_xrdp_display_and_relative_authority_from_process(target_module, tmp_path, monkeypatch):
    proc, home = tmp_path / 'proc', tmp_path / 'session-home'
    proc.mkdir()
    home.mkdir()
    for pid, command in {
        '123': b'/usr/lib/xorg/Xorg\0:27\0-auth\0private-authority\0-config\0xrdp/xorg.conf\0',
        '456': b'/usr/bin/Xtigervnc\0:91\0',
    }.items():
        entry = proc / pid
        entry.mkdir()
        (entry / 'cmdline').write_bytes(command)
        (entry / 'cwd').symlink_to(home)
    monkeypatch.setattr(target_module, 'Path', lambda value: proc if value == '/proc' else Path(value))
    assert target_module._xrdp_sessions(os.geteuid()) == {123: (':27', str(home / 'private-authority'))}
