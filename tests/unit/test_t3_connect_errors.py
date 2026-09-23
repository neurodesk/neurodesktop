"""Only allowlisted T3 startup errors reach the Connect UI."""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from testlib import repo_path

sys.path.insert(0, str(repo_path('extensions/t3-code-server')))
from neurodesk_t3_code import supervisor
from neurodesk_t3_code import connect as connect_module
from neurodesk_t3_code.connect import ConnectError, ConnectManager

CONNECTIONS_PAGE = 'https://app.t3.codes/settings/general'


@pytest.mark.parametrize('value,expected', [
    (b'Relay managed tunnel limit reached: this account allows at most 3 tunnels', 3),
    (b'T3 Connect: Relay managed tunnel limit reached: this account allows at most 12 tunnels. Trace ID: private', 12),
    (b'403 Forbidden: token=secret', None),
    (b'environment_link_limit_exceeded', None),
    (b'Relay managed tunnel limit reached: this account allows at most unknown tunnels', None),
])
def test_only_known_quota_message_is_recognized(value, expected):
    assert supervisor.relay_tunnel_limit(value) == expected


def test_quota_message_uses_actual_limit_without_waiting_for_routing():
    async def scenario():
        manager = ConnectManager(SimpleNamespace(connect_tunnel_limit=12))
        await manager._run(manager._wait_reachable)
        assert manager.state == 'error'
        assert 'limit of 12 managed tunnels' in manager.message
        assert 'Disconnect an unused environment' in manager.message
        assert 'T3 support' in manager.message
        assert 'Retry' in manager.message
        assert 'sign-in is saved' in manager.message
        assert manager.snapshot()['connections_url'] == CONNECTIONS_PAGE
    asyncio.run(scenario())


def test_unreachable_relay_names_the_connection_limit_and_its_settings_page(monkeypatch):
    monkeypatch.setattr(connect_module, 'ROUTE_TIMEOUT', 0)
    async def scenario():
        manager = ConnectManager(SimpleNamespace(connect_tunnel_limit=None))
        await manager._run(manager._wait_reachable)
        assert manager.state == 'error'
        assert 'only 3 connected environments' in manager.message
        assert manager.snapshot()['connections_url'] == CONNECTIONS_PAGE
    asyncio.run(scenario())


def test_unrelated_failures_do_not_advertise_the_connections_page():
    async def scenario():
        manager = ConnectManager(SimpleNamespace())
        async def limited():
            raise ConnectError('At capacity.', connections_url=CONNECTIONS_PAGE)
        async def unrelated():
            raise ConnectError('The relay client is missing from this image.')
        await manager._run(limited)
        assert manager.snapshot()['connections_url'] == CONNECTIONS_PAGE
        await manager._run(unrelated)
        assert manager.snapshot()['connections_url'] is None
    asyncio.run(scenario())


def test_real_child_stderr_quota_reaches_ui_and_resets_after_restart(tmp_path, monkeypatch):
    """Exercise quota classification and recovery through an actual child process."""
    fake = tmp_path / 'fake-t3'
    fake.write_text('''#!/usr/bin/env python3
import pathlib, socket, sys, time
port = int(sys.argv[sys.argv.index('--port') + 1])
marker = pathlib.Path('already-reported')
if not marker.exists():
    sys.stderr.write('untrusted OAuth token=secret' * 2000)
    sys.stderr.write('\\nFailed to reconcile T3 Connect desired link on startup\\n')
    sys.stderr.flush()
    sys.stderr.write('T3 Connect: Relay managed tunnel limit reached: this account ')
    sys.stderr.flush()
    time.sleep(.05)
    sys.stderr.write('allows at most 3 tunnels. Trace ID: private-trace\\n')
    sys.stderr.flush()
    marker.touch()
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('127.0.0.1', port))
sock.listen()
while True:
    client, _ = sock.accept()
    with client:
        if client.recv(4096):
            client.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\n{}')
''')
    fake.chmod(0o755)
    providers = tmp_path / 'providers'
    providers.mkdir()
    env = {'HOME': str(tmp_path), 'PATH': os.environ['PATH'],
        'NEURODESKTOP_T3_CODE_EXECUTABLE': str(fake),
        'NEURODESKTOP_T3_CODE_PROVIDER_BIN': str(providers),
        'NEURODESKTOP_T3_CODE_PORT': str(supervisor.find_free_port())}
    async def scenario():
        service = supervisor.T3Supervisor(supervisor.policy_from_environment(env), environ=env)
        try:
            service.start()
            await service.wait_ready()
            manager = ConnectManager(service)
            await asyncio.wait_for(manager._run(manager._wait_reachable), 2)
            assert manager.state == 'error'
            assert 'limit of 3 managed tunnels' in manager.message
            assert 'secret' not in str(manager.snapshot())
            assert 'private-trace' not in str(manager.snapshot())
            await service.close()
            assert service._output_reader is None
            service.start()
            await service.wait_ready()
            assert service.connect_tunnel_limit is None
        finally:
            await service.close()
    asyncio.run(scenario())
