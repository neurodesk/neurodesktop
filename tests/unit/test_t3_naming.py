"""Hub display names never alter identity, DNS or user connection aliases."""
import asyncio
import os
from pathlib import Path
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
