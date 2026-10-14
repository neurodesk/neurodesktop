import ctypes
import ctypes.util
import os
import select
import signal
import subprocess
import time
from pathlib import Path


class _WindowAttributes(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int) for name in ("x", "y", "width", "height", "border_width", "depth")
    ] + [
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong),
        ("window_class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("display", ctypes.c_void_p), ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte), ("minor_code", ctypes.c_ubyte),
    ]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))
_IO_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_IO_ERROR_EXIT_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)


def start_display(log, geometry="1280x1024"):
    """Start Xtigervnc on a free display and return the process and display name."""
    # With -displayfd, the server skips every display where any listener fails.
    # Some sandboxes refuse abstract Unix sockets (Xtrans "local"), so no display
    # qualifies; retry with the file socket alone. Keep the abstract listener
    # otherwise, so a host-network container cannot reuse a host display's name.
    for disabled in (("tcp",), ("tcp", "local")):
        reader, writer = os.pipe()
        try:
            process = subprocess.Popen(
                ["/usr/local/bin/Xtigervnc", "-displayfd", str(writer),
                 "-geometry", geometry, "-depth", "24", "-SecurityTypes", "None",
                 "-rfbport", "-1"] + [arg for name in disabled for arg in ("-nolisten", name)],
                pass_fds=(writer,), stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            os.close(writer)
        try:
            deadline = time.monotonic() + 20
            announced = b""
            # X servers can write the digits and newline separately. Closing the
            # reader after the digits makes the server's final write fail.
            while b"\n" not in announced and len(announced) < 100:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([reader], [], [], remaining)[0]:
                    break
                chunk = os.read(reader, 100 - len(announced))
                if not chunk:
                    break
                announced += chunk
        finally:
            os.close(reader)
        if announced.endswith(b"\n") and announced[:-1].isdigit():
            return process, ":" + announced[:-1].decode("ascii")
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    log.flush()
    raise AssertionError("Virtual display did not announce its display number\n"
                         + Path(log.name).read_text(errors="replace"))


class Desktop:
    def __init__(self, directory):
        self.directory = directory
        self.log = (directory / "display.log").open("w")
        self.errors = []
        self._previous_handlers = None
        self.lost = False
        self.process = self.connection = None
        try:
            self.process, self.name = start_display(self.log)
            self._connect()
        except BaseException:
            self.close()
            raise

    def _connect(self):
        self.x = ctypes.CDLL(ctypes.util.find_library("X11"))
        self.xtest = ctypes.CDLL(ctypes.util.find_library("Xtst"))
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x.XGetErrorText.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        self.x.XSetErrorHandler.restype = ctypes.c_void_p
        self.x.XSetIOErrorHandler.argtypes = [ctypes.c_void_p]
        self.x.XSetIOErrorHandler.restype = ctypes.c_void_p
        self.x.XSetIOErrorExitHandler.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        # Xlib's default handlers call exit(1), which ends pytest without a report.
        self._handlers = (_ERROR_HANDLER(self._record_error),
                          _IO_ERROR_HANDLER(self._record_lost_connection),
                          _IO_ERROR_EXIT_HANDLER(lambda display, data: None))
        self._previous_handlers = (
            self.x.XSetErrorHandler(ctypes.cast(self._handlers[0], ctypes.c_void_p)),
            self.x.XSetIOErrorHandler(ctypes.cast(self._handlers[1], ctypes.c_void_p)),
        )
        deadline = time.monotonic() + 20
        self.connection = None
        while not self.connection and self.process.poll() is None and time.monotonic() < deadline:
            self.connection = self.x.XOpenDisplay(self.name.encode())
            if not self.connection:
                time.sleep(0.1)
        assert self.connection, "Cannot connect to the virtual display"
        self.x.XSetIOErrorExitHandler(self.connection, ctypes.cast(self._handlers[2], ctypes.c_void_p), None)
        self.x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
        self.x.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
        self.x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_WindowAttributes)]
        self.x.XFree.argtypes = [ctypes.c_void_p]
        self.x.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x.XKeysymToKeycode.restype = ctypes.c_uint
        self.x.XFlush.argtypes = [ctypes.c_void_p]
        self.x.XGrabServer.argtypes = [ctypes.c_void_p]
        self.x.XUngrabServer.argtypes = [ctypes.c_void_p]
        self.xtest.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.xtest.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        self.xtest.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

    def _record_error(self, display, event):
        text = ctypes.create_string_buffer(256)
        self.x.XGetErrorText(display, event.contents.error_code, text, len(text))
        self.errors.append(f"{text.value.decode()} for request {event.contents.request_code}"
                           f" on resource {event.contents.resourceid:#x}")
        return 0

    def _record_lost_connection(self, display):
        self.lost = True
        return 0

    def _check(self):
        # Protocol errors arrive asynchronously, so collect them after a round trip.
        if not self.lost:
            self.x.XSync(self.connection, 0)
        if self.lost or self.errors:
            raise AssertionError(
                f"X display {self.name} "
                + ("closed its connection" if self.lost else f"rejected requests: {self.errors}")
                + "\n" + (self.directory / "display.log").read_text(errors="replace"))

    def _windows(self):
        root = self.x.XDefaultRootWindow(self.connection)
        parent = ctypes.c_ulong()
        returned_root = ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        self.x.XQueryTree(self.connection, root, ctypes.byref(returned_root),
            ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count))
        result = {}
        try:
            for index in range(count.value):
                title = ctypes.c_char_p()
                attributes = _WindowAttributes()
                self.x.XGetWindowAttributes(self.connection, children[index], ctypes.byref(attributes))
                if attributes.map_state != 2:
                    continue
                if self.x.XFetchName(self.connection, children[index], ctypes.byref(title)):
                    try:
                        if title.value:
                            result[children[index]] = title.value.decode(errors="replace")
                    finally:
                        self.x.XFree(title)
        finally:
            if children:
                self.x.XFree(children)
        return result

    def focus(self, title, process, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert process.poll() is None, "Application exited before its document window appeared"
            # A client can destroy its window between the tree snapshot and focus.
            self.x.XGrabServer(self.connection)
            try:
                windows = self._windows()
                for window, name in windows.items():
                    if title in name:
                        self.x.XSetInputFocus(self.connection, window, 1, 0)
                        return
            finally:
                self.x.XUngrabServer(self.connection)
                self._check()
            time.sleep(0.1)
        raise AssertionError(f"No window containing {title!r}; visible titles were {windows}")

    def chord(self, *keysyms):
        codes = [self.x.XKeysymToKeycode(self.connection, key) for key in keysyms]
        assert all(codes), f"No X11 keycode for {keysyms}"
        for code in codes:
            self.xtest.XTestFakeKeyEvent(self.connection, code, 1, 0)
        for code in reversed(codes):
            self.xtest.XTestFakeKeyEvent(self.connection, code, 0, 0)
        self._check()

    def text(self, value):
        for character in value:
            modifiers = [0xFFE1] if character.isupper() or character in '~!@#$%^&*()_+{}|:"<>?' else []
            self.chord(*modifiers, ord(character))

    def screenshot(self, path):
        from PIL import ImageGrab

        ImageGrab.grab(xdisplay=self.name).save(path)

    def click(self, x, y):
        self.xtest.XTestFakeMotionEvent(self.connection, -1, x, y, 0)
        self.xtest.XTestFakeButtonEvent(self.connection, 1, 1, 0)
        self.xtest.XTestFakeButtonEvent(self.connection, 1, 0, 0)
        self._check()

    def close(self):
        if self.connection and not self.lost:
            self.x.XCloseDisplay(self.connection)
        if self._previous_handlers is not None:
            self.x.XSetErrorHandler(self._previous_handlers[0])
            self.x.XSetIOErrorHandler(self._previous_handlers[1])
            self._previous_handlers = None
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.log.close()
