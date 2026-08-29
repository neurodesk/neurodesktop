"""Behavioral contract of the kernel websocket bridge nudge.

The module under test is installed into ``jupyter_server_documents`` by
``patch_jupyter_server_documents.py``; ``websocket_connection.py`` awaits it
between ``start_channels()`` and starting the channel listen tasks. It must
prove a shell/control round trip and a live IOPub subscription, keep retrying
lost requests, forward (never swallow) the IOPub message it observes, always
release its transient sockets, and never raise into ``connect()``.
"""

import asyncio
import logging
import types

import zmq
from jupyter_server.services.kernels.connection.base import (
    serialize_msg_to_ws_v1,
)

from testlib import load_source_module


def load_nudge_module():
    return load_source_module(
        "neurodesktop_kernel_nudge",
        "/opt/neurodesktop/neurodesktop_kernel_nudge.py",
        "config/jupyter/neurodesktop_kernel_nudge.py",
    )


IOPUB_RAW = [b"signature", b"header", b"parent", b"metadata", b"content"]
IOPUB_FORWARDED = serialize_msg_to_ws_v1(IOPUB_RAW[1:], "iopub")


class FakeSocket:
    def __init__(self):
        self.messages = []
        self.closed = False
        self.close_linger = None

    def deliver(self, msg_list):
        self.messages.append(list(msg_list))

    async def recv_multipart(self):
        return self.messages.pop(0)

    def close(self, linger=None):
        self.closed = True
        self.close_linger = linger


class FakePoller:
    def __init__(self):
        self.sockets = []

    def register(self, socket, flags):
        self.sockets.append(socket)

    async def poll(self, timeout_ms):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000
        while True:
            ready = [
                (socket, zmq.POLLIN)
                for socket in self.sockets
                if socket.messages
            ]
            if ready or loop.time() >= deadline:
                return ready
            await asyncio.sleep(0.001)


class FakeSession:
    def __init__(self, on_send=None):
        self.sent = []
        self.on_send = on_send

    def send(self, socket, msg_type):
        self.sent.append((socket, msg_type))
        if self.on_send is not None:
            self.on_send(socket, msg_type)

    def feed_identities(self, msg_list):
        return [], list(msg_list)


class FakeClient:
    def __init__(self, session):
        self.session = session
        self.shell = FakeSocket()
        self.control = FakeSocket()
        self.iopub_channel = types.SimpleNamespace(socket=FakeSocket())

    def connect_shell(self):
        return self.shell

    def connect_control(self):
        return self.control


class FakeWebsocketHandler:
    def __init__(self):
        self.written = []

    def write_message(self, message, binary=False):
        self.written.append((message, binary))


class FakeConnection:
    def __init__(self, client, execution_state="idle"):
        self._client = client
        self.kernel_manager = types.SimpleNamespace(
            execution_state=execution_state
        )
        self.websocket_handler = FakeWebsocketHandler()
        self.log = logging.getLogger("neurodesktop-kernel-nudge-test")
        self.kernel_id = "kernel-under-test"


def test_nudge_proves_reply_and_forwards_the_iopub_message():
    nudge_module = load_nudge_module()
    session = FakeSession()

    def answer(socket, msg_type):
        socket.deliver([b"signature", b"kernel_info_reply"])
        client.iopub_channel.socket.deliver(IOPUB_RAW)

    session.on_send = answer
    client = FakeClient(session)
    connection = FakeConnection(client)

    outcome = asyncio.run(
        nudge_module.nudge(connection, timeout=5, poller=FakePoller())
    )

    assert outcome == "ready"
    # The proving IOPub message belongs to the frontend and is forwarded
    # exactly as the listen tasks would forward it, never consumed.
    assert connection.websocket_handler.written == [(IOPUB_FORWARDED, True)]
    # Transient request sockets are always released; the client's own IOPub
    # socket stays open for the listen task that follows.
    assert client.shell.closed and client.shell.close_linger == 0
    assert client.control.closed and client.control.close_linger == 0
    assert not client.iopub_channel.socket.closed


def test_nudge_resends_a_lost_request_until_it_is_answered():
    nudge_module = load_nudge_module()
    shell_sends = 0

    def answer_second_request(socket, msg_type):
        nonlocal shell_sends
        if socket is not client.shell:
            return
        shell_sends += 1
        if shell_sends >= 2:
            socket.deliver([b"signature", b"kernel_info_reply"])
            client.iopub_channel.socket.deliver(IOPUB_RAW)

    session = FakeSession(on_send=answer_second_request)
    client = FakeClient(session)
    connection = FakeConnection(client)

    outcome = asyncio.run(
        nudge_module.nudge(
            connection, timeout=5, resend_interval=0.01, poller=FakePoller()
        )
    )

    assert outcome == "ready"
    assert shell_sends >= 2


def test_nudge_times_out_without_both_proofs_and_releases_sockets():
    nudge_module = load_nudge_module()

    def answer_shell_only(socket, msg_type):
        if socket is client.shell and not client.shell.messages:
            socket.deliver([b"signature", b"kernel_info_reply"])

    session = FakeSession(on_send=answer_shell_only)
    client = FakeClient(session)
    connection = FakeConnection(client)

    outcome = asyncio.run(
        nudge_module.nudge(
            connection, timeout=0.05, resend_interval=0.01, poller=FakePoller()
        )
    )

    assert outcome == "timeout"
    assert client.shell.closed and client.control.closed
    assert connection.websocket_handler.written == []


def test_nudge_skips_a_busy_kernel_without_sending():
    nudge_module = load_nudge_module()
    session = FakeSession()
    client = FakeClient(session)
    connection = FakeConnection(client, execution_state="busy")

    outcome = asyncio.run(nudge_module.nudge(connection, poller=FakePoller()))

    assert outcome == "busy-skipped"
    assert session.sent == []
    assert not client.shell.closed and not client.control.closed


def test_nudge_never_raises_into_connect():
    nudge_module = load_nudge_module()

    class BrokenClient(FakeClient):
        def connect_shell(self):
            raise RuntimeError("no transient socket")

    connection = FakeConnection(BrokenClient(FakeSession()))

    outcome = asyncio.run(nudge_module.nudge(connection, poller=FakePoller()))

    assert outcome == "error"
