import ctypes as c
import ctypes.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time

import websocket

from native_desktop_driver import _WindowAttributes


def _xrdp_sessions(uid):
    if os.geteuid() == 0 and uid != 0:
        # Container root may lack ptrace access to the notebook user's /proc
        # symlinks. Discover the authority path as the session owner as well.
        probe = subprocess.run(
            [sys.executable, __file__, '--sessions'],
            user=uid, group=pwd.getpwuid(uid).pw_gid, extra_groups=[],
            capture_output=True, text=True, timeout=2,
        )
        assert probe.returncode == 0, f'XRDP session discovery failed: {probe.stderr}'
        return {int(pid): tuple(session) for pid, session in json.loads(probe.stdout).items()}
    sessions = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            args = proc.joinpath('cmdline').read_bytes().decode().split('\0')
            if Path(args[0]).name != 'Xorg' or 'xrdp/xorg.conf' not in args:
                continue
            display = next(arg for arg in args if arg.startswith(':'))
            authority = Path(args[args.index('-auth') + 1])
            if not authority.is_absolute():
                authority = proc.joinpath('cwd').readlink() / authority
            sessions[int(proc.name)] = (display, str(authority))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return sessions


class RdpKeyboardTarget:
    def __init__(self, uid):
        self.uid = uid
        self.previous_sessions = _xrdp_sessions(uid)
        self.session = None

    def snapshot(self, timeout_seconds):
        sessions = _xrdp_sessions(self.uid)
        if self.session is None:
            fresh = sessions.keys() - self.previous_sessions.keys()
            assert len(fresh) <= 1, f'Ambiguous new XRDP sessions: {sorted(fresh)}'
            if not fresh:
                return {'waiting': 'new notebook-user XRDP session'}
            pid = fresh.pop()
            self.session = (pid, sessions[pid])
        pid, (display, authority) = self.session
        assert sessions.get(pid) == (display, authority), f'XRDP session {pid} exited or changed'
        # Isolate Xlib's process-wide handlers and XAUTHORITY from pytest. A dead
        # X server must fail this probe, not terminate pytest in Xlib's handler.
        probe = subprocess.run(
            [sys.executable, __file__, display],
            env={**os.environ, 'XAUTHORITY': authority},
            user=self.uid, group=pwd.getpwuid(self.uid).pw_gid,
            extra_groups=[] if os.geteuid() == 0 else None,
            capture_output=True, text=True, timeout=timeout_seconds,
        )
        assert probe.returncode == 0, f'X11 observation failed on {display}: {probe.stderr}'
        return {'display': display, 'pid': pid, **json.loads(probe.stdout)}

    def wait(self, tunnel, *, run_dialog=False, timeout_seconds=30):
        expected = 'focused Run dialog' if run_dialog else 'desktop hotkey target'
        field = 'run_focused' if run_dialog else 'hotkey_ready'
        deadline = time.monotonic() + timeout_seconds
        state = {'waiting': 'first X11 observation'}
        previous_timeout = tunnel.gettimeout()
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                tunnel.settimeout(min(0.1, remaining))
                try:
                    frame = tunnel.recv()
                except websocket.WebSocketTimeoutException:
                    pass
                except Exception as error:
                    raise AssertionError(f'Guacamole transport failed waiting for {expected}; {state}') from error
                else:
                    assert frame, f'Guacamole tunnel closed waiting for {expected}; {state}'
                    assert not isinstance(frame, str) or '.error,' not in frame, (
                        f'Guacamole error waiting for {expected}: {frame!r}; {state}'
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    state = self.snapshot(min(2, remaining))
                except subprocess.TimeoutExpired as error:
                    raise AssertionError(f'X11 observation timed out waiting for {expected}; {state}') from error
                if state.get(field) and time.monotonic() < deadline:
                    return state
        finally:
            tunnel.settimeout(previous_timeout)
        raise AssertionError(f'No {expected} within {timeout_seconds}s; last X11 state: {state}')


def _snapshot(display):
    x = c.CDLL(ctypes.util.find_library('X11'))
    x.XOpenDisplay.argtypes = [c.c_char_p]
    x.XOpenDisplay.restype = c.c_void_p
    x.XDefaultRootWindow.argtypes = [c.c_void_p]
    x.XDefaultRootWindow.restype = c.c_ulong
    x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
    x.XInternAtom.restype = c.c_ulong
    x.XGetWindowProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_long,
        c.c_long, c.c_int, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_int),
        c.POINTER(c.c_ulong), c.POINTER(c.c_ulong), c.POINTER(c.c_void_p)]
    x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong),
        c.POINTER(c.c_ulong), c.POINTER(c.POINTER(c.c_ulong)), c.POINTER(c.c_uint)]
    x.XFetchName.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_void_p)]
    x.XGetWindowAttributes.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(_WindowAttributes)]
    x.XGetInputFocus.argtypes = [c.c_void_p, c.POINTER(c.c_ulong), c.POINTER(c.c_int)]
    for name in ('XGrabServer', 'XUngrabServer', 'XCloseDisplay', 'XFree'):
        getattr(x, name).argtypes = [c.c_void_p]
    connection = x.XOpenDisplay(display.encode())
    assert connection, f'Cannot open XRDP display {display}'

    def windows_property(window, name):
        actual, form, size, remaining, data = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong(), c.c_void_p()
        atom = x.XInternAtom(connection, name.encode(), 1)
        if not atom:
            return []
        status = x.XGetWindowProperty(connection, window, atom, 0, 1024, 0, 0,
            c.byref(actual), c.byref(form), c.byref(size), c.byref(remaining), c.byref(data))
        try:
            return list(c.cast(data, c.POINTER(c.c_ulong))[:size.value]) if status == 0 and data and form.value == 32 else []
        finally:
            if data:
                x.XFree(data)

    def parent(window):
        root, result, children, count = c.c_ulong(), c.c_ulong(), c.POINTER(c.c_ulong)(), c.c_uint()
        assert x.XQueryTree(connection, window, c.byref(root), c.byref(result), c.byref(children), c.byref(count))
        if children:
            x.XFree(children)
        return result.value

    try:
        # Keep window destruction/reparenting from invalidating the focus walk.
        x.XGrabServer(connection)
        root = x.XDefaultRootWindow(connection)
        wm = windows_property(root, '_NET_SUPPORTING_WM_CHECK')
        wm_registered = bool(wm and windows_property(wm[0], '_NET_SUPPORTING_WM_CHECK') == wm)
        active = windows_property(root, '_NET_ACTIVE_WINDOW')
        focus, revert = c.c_ulong(), c.c_int()
        x.XGetInputFocus(connection, c.byref(focus), c.byref(revert))
        mapped = {}
        for window in windows_property(root, '_NET_CLIENT_LIST'):
            attributes, title = _WindowAttributes(), c.c_void_p()
            x.XGetWindowAttributes(connection, window, c.byref(attributes))
            if attributes.map_state == 2 and x.XFetchName(connection, window, c.byref(title)) and title:
                mapped[window] = c.string_at(title).decode(errors='replace')
                x.XFree(title)
        ancestors = []
        window = focus.value
        while window not in (0, 1, root):
            window = parent(window)
            ancestors.append(window)
        return {
            'wm': wm, 'wm_registered': wm_registered, 'active': active,
            'focus': focus.value, 'focus_ancestors': ancestors, 'mapped': mapped,
            'hotkey_ready': wm_registered and 'panel' in mapped.values(),
            'run_focused': wm_registered and any(
                title == 'Run' and active == [window] and window in ancestors
                for window, title in mapped.items()
            ),
        }
    finally:
        x.XUngrabServer(connection)
        x.XCloseDisplay(connection)


if __name__ == '__main__':
    print(json.dumps(_xrdp_sessions(os.geteuid()) if sys.argv[1] == '--sessions' else _snapshot(sys.argv[1])))
