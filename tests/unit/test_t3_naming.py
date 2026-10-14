"""Hub display names never alter identity, DNS or user connection aliases."""
import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from testlib import repo_path

sys.path.insert(0, str(repo_path('extensions/t3-code-server')))
from neurodesk_t3_code.naming import environment_label, remember_public_host
from neurodesk_t3_code.connect import ConnectManager


@pytest.mark.parametrize('key', ['JUPYTERHUB_PUBLIC_HUB_URL', 'JUPYTERHUB_PUBLIC_URL', 'JUPYTERHUB_HOST'])
def test_configured_hub_hostname(tmp_path, key):
    policy = SimpleNamespace(base_dir=tmp_path)
    env = {'JUPYTERHUB_USER': 'akshitbeniwal', key: 'https://edu.neurodesk.org/user/akshitbeniwal/'}
    assert environment_label(policy, env) == 'akshitbeniwal@edu.neurodesk.org'
    env['JUPYTERHUB_SERVER_NAME'] = 'gpu'
    assert environment_label(policy, env) == 'akshitbeniwal/gpu@edu.neurodesk.org'


def test_remembered_authenticated_host_and_custom_label(tmp_path):
    policy = SimpleNamespace(base_dir=tmp_path)
    env = {'JUPYTERHUB_USER': 'stebo85'}
    assert environment_label(policy, env) == ''
    remember_public_host(policy, env, 'edu.neurodesk.org:443')
    assert environment_label(policy, env) == 'stebo85@edu.neurodesk.org'
    assert (tmp_path / 'neurodesktop-public-host').stat().st_mode & 0o777 == 0o600
    env['NEURODESKTOP_T3_CODE_LABEL'] = 'My custom server'
    assert environment_label(policy, env) == 'My custom server'
    assert environment_label(policy, {}) == ''


@pytest.mark.parametrize('host', ['bad\nhost', 'user:password@example.org', '/internal', ''])
def test_invalid_hostname_not_persisted(tmp_path, host):
    remember_public_host(SimpleNamespace(base_dir=tmp_path), {'JUPYTERHUB_USER': 'stebo85'}, host)
    assert not (tmp_path / 'neurodesktop-public-host').exists()


def test_wrapper_only_overrides_pretty_probe():
    wrapper = repo_path('config/agents/t3-provider-bin/hostnamectl')
    result = subprocess.run(['sh', str(wrapper), '--pretty'], env={**os.environ,
        'NEURODESKTOP_T3_CODE_LABEL': 'stebo85@edu.neurodesk.org'}, capture_output=True, text=True, check=True)
    assert result.stdout == 'stebo85@edu.neurodesk.org\n'


@pytest.mark.parametrize('busy', [False, True])
def test_existing_connection_renamed_only_when_idle(tmp_path, busy):
    async def scenario():
        service = SimpleNamespace(policy=SimpleNamespace(base_dir=tmp_path),
            environ={'JUPYTERHUB_USER': 'stebo85'}, close=AsyncMock(), start=lambda: None,
            wait_ready=AsyncMock())
        manager = ConnectManager(service)
        manager.active_chats = lambda: busy
        await manager.configure_name('edu.neurodesk.org')
        assert manager._name_pending == busy
        assert service.close.await_count == (0 if busy else 1)
        # Remembered names survive a restart without changing T3 environment-id or credentials.
        assert environment_label(service.policy, service.environ) == 'stebo85@edu.neurodesk.org'
        manager.active_chats = lambda: False
        await manager.configure_name('edu.neurodesk.org')
        assert service.close.await_count == 1
        await manager.configure_name('edu.neurodesk.org')
        assert service.close.await_count == 1
    asyncio.run(scenario())


def test_explicit_name_never_restarts_service(tmp_path):
    async def scenario():
        service = SimpleNamespace(policy=SimpleNamespace(base_dir=tmp_path),
            environ={'JUPYTERHUB_USER': 'stebo85', 'NEURODESKTOP_T3_CODE_LABEL': 'My lab'},
            close=AsyncMock())
        manager = ConnectManager(service)
        await manager.configure_name('edu.neurodesk.org')
        assert not manager._name_pending
        service.close.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('latest_host', ['a.example.org', 'b.example.org'])
def test_overlapping_name_requests_apply_latest_label(tmp_path, latest_host):
    async def scenario():
        first_started = asyncio.Event()
        release_ready = asyncio.Event()
        second_requested = asyncio.Event()
        spawned_labels = []
        policy = SimpleNamespace(base_dir=tmp_path)
        environ = {'JUPYTERHUB_USER': 'user'}

        def start():
            spawned_labels.append(environment_label(policy, environ))

        async def wait_ready():
            if len(spawned_labels) == 1:
                first_started.set()
                await release_ready.wait()

        service = SimpleNamespace(policy=policy, environ=environ,
            close=AsyncMock(), start=start, wait_ready=wait_ready)
        manager = ConnectManager(service)
        manager.active_chats = lambda: False

        async def request_latest():
            second_requested.set()
            await manager.configure_name(latest_host)

        first = asyncio.create_task(manager.configure_name('a.example.org'))
        await first_started.wait()
        second = asyncio.create_task(request_latest())
        await second_requested.wait()
        release_ready.set()
        await asyncio.gather(first, second)

        assert spawned_labels[-1] == f'user@{latest_host}'
        if latest_host == 'a.example.org':
            assert len(spawned_labels) == 1

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_pending_name_applies_during_connection_check_without_relink(tmp_path):
    async def scenario():
        service = SimpleNamespace(policy=SimpleNamespace(base_dir=tmp_path),
            environ={'JUPYTERHUB_USER': 'stebo85'}, close=AsyncMock(), start=lambda: None,
            wait_ready=AsyncMock())
        manager = ConnectManager(service)
        manager.active_chats = lambda: True
        await manager.configure_name('edu.neurodesk.org')
        manager.active_chats = lambda: False
        manager.reachable = AsyncMock(return_value=True)
        manager._process = AsyncMock()
        await manager._wait_reachable()
        assert manager.state == 'ready'
        service.close.assert_awaited_once()
        manager._process.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('operation', ['_wait_reachable', '_link'])
def test_name_requested_during_background_restart_reaches_running_service(tmp_path, operation):
    async def scenario():
        first_started = asyncio.Event()
        release_ready = asyncio.Event()
        latest_requested = asyncio.Event()
        release_route = asyncio.Event()
        spawned_labels = []
        policy = SimpleNamespace(base_dir=tmp_path)
        environ = {'JUPYTERHUB_USER': 'user'}

        async def wait_ready():
            first_started.set()
            await release_ready.wait()

        async def reachable():
            await release_route.wait()
            return True

        service = SimpleNamespace(policy=policy, environ=environ, close=AsyncMock(),
            start=lambda: spawned_labels.append(environment_label(policy, environ)),
            wait_ready=wait_ready)
        manager = ConnectManager(service)
        manager.active_chats = lambda: True
        await manager.configure_name('a.example.org')
        manager.active_chats = lambda: False
        manager.reachable = reachable
        manager.saved_status = AsyncMock(return_value={
            'relayClient': {'status': 'available'}, 'desired': True,
            'authenticated': True, 'linked': False})
        manager._process = AsyncMock(return_value=b'')
        manager.task = asyncio.create_task(manager._run(getattr(manager, operation)))
        await first_started.wait()

        async def request_latest():
            latest_requested.set()
            await manager.configure_name('b.example.org')

        latest = asyncio.create_task(request_latest())
        await latest_requested.wait()
        release_ready.set()
        await latest
        assert not manager.task.done()
        release_route.set()
        await manager.task
        await manager.configure_name('b.example.org')

        assert spawned_labels == ['user@a.example.org', 'user@b.example.org']
        assert manager.state == 'ready'

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
