"""Prove a fresh kernel WebSocket bridge end-to-end before it carries traffic.

``jupyter-server-documents`` replaces jupyter_server's kernel WebSocket
connection with a per-connection ``AsyncKernelClient``. Upstream
jupyter_server "nudges" every new connection: it repeats
``kernel_info_request`` until a shell or control reply and at least one IOPub
message arrive, because a freshly connected ZMQ SUB socket silently drops
everything published before its subscription reaches the kernel (the
"slow joiner" race). The replacement bridge skips that step, so a client can
send requests whose IOPub replies are lost with no error anywhere — the
frontend then waits forever for state that will never arrive.

This module restores the nudge for the per-connection bridge. Requests go out
on transient shell and control sockets so their replies cannot leak to the
frontend, and the IOPub message that proves the subscription is forwarded to
the WebSocket instead of consumed, so no real broadcast is lost. A kernel
that is already executing cannot answer promptly and is skipped, matching
upstream. On timeout or error the connection proceeds with the old behavior.

The patcher installs this module into the package as
``jupyter_server_documents/_neurodesktop_kernel_nudge.py``; the anchored
change in ``websocket_connection.py`` only awaits :func:`nudge`.
"""

from __future__ import annotations

import asyncio

import zmq
import zmq.asyncio
from jupyter_server.services.kernels.connection.base import (
    serialize_msg_to_ws_v1,
)
from tornado.websocket import WebSocketClosedError


NUDGE_TIMEOUT_SECONDS = 10.0
NUDGE_RESEND_SECONDS = 0.5


def forward_iopub_message(connection, msg_list) -> None:
    """Forward one raw IOPub message exactly the way the listen tasks do."""
    _, fed = connection._client.session.feed_identities(msg_list)
    parts = fed[1:]  # strip signature frame
    try:
        bin_msg = serialize_msg_to_ws_v1(parts, "iopub")
        connection.websocket_handler.write_message(bin_msg, binary=True)
    except WebSocketClosedError:
        pass
    except Exception as err:
        connection.log.error("Error forwarding kernel message: %s", err)


async def nudge(
    connection,
    *,
    timeout: float = NUDGE_TIMEOUT_SECONDS,
    resend_interval: float = NUDGE_RESEND_SECONDS,
    poller=None,
) -> str:
    """Nudge *connection*'s kernel bridge; return the outcome for logging.

    Outcomes: ``"ready"`` (a shell or control reply and one IOPub message
    proved the bridge), ``"busy-skipped"``, ``"timeout"``, or ``"error"``.
    The connection is usable after every outcome; only ``"ready"`` proves it.
    """
    if getattr(connection.kernel_manager, "execution_state", None) == "busy":
        # Shell kernel-info requests queue behind the running execution and
        # a busy kernel has long since established its subscriptions.
        return "busy-skipped"
    client = connection._client
    shell_socket = None
    control_socket = None
    try:
        shell_socket = client.connect_shell()
        control_socket = client.connect_control()
        iopub_socket = client.iopub_channel.socket
        if poller is None:
            poller = zmq.asyncio.Poller()
        poller.register(shell_socket, zmq.POLLIN)
        poller.register(control_socket, zmq.POLLIN)
        poller.register(iopub_socket, zmq.POLLIN)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        reply_seen = False
        iopub_seen = False
        while not (reply_seen and iopub_seen):
            now = loop.time()
            if now >= deadline:
                connection.log.warning(
                    "Kernel websocket nudge timed out after %.1fs for kernel "
                    "%s (shell/control reply: %s, iopub message: %s); "
                    "continuing without proof of the bridge.",
                    timeout,
                    connection.kernel_id,
                    reply_seen,
                    iopub_seen,
                )
                return "timeout"
            # Re-send every round: each request also triggers the IOPub
            # status broadcast that proves the subscription.
            client.session.send(shell_socket, "kernel_info_request")
            client.session.send(control_socket, "kernel_info_request")
            round_deadline = min(now + resend_interval, deadline)
            while not (reply_seen and iopub_seen):
                wait_seconds = round_deadline - loop.time()
                if wait_seconds <= 0:
                    break
                events = dict(
                    await poller.poll(max(1, int(wait_seconds * 1000)))
                )
                if events.get(shell_socket, 0) & zmq.POLLIN:
                    await shell_socket.recv_multipart()
                    reply_seen = True
                if events.get(control_socket, 0) & zmq.POLLIN:
                    await control_socket.recv_multipart()
                    reply_seen = True
                if events.get(iopub_socket, 0) & zmq.POLLIN:
                    forward_iopub_message(
                        connection, await iopub_socket.recv_multipart()
                    )
                    iopub_seen = True
        return "ready"
    except Exception:
        connection.log.exception(
            "Kernel websocket nudge failed for kernel %s; continuing "
            "without it.",
            connection.kernel_id,
        )
        return "error"
    finally:
        for transient_socket in (shell_socket, control_socket):
            if transient_socket is not None:
                transient_socket.close(linger=0)
