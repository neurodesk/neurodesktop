"""Exercise the guided linking lifecycle without accounts or outbound traffic."""
import asyncio
import json
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from testlib import repo_path

sys.path.insert(0, str(repo_path('extensions/t3-code-server')))
from neurodesk_t3_code.connect import ConnectManager, ConnectError
from tornado.web import HTTPError


@pytest.fixture
def manager(tmp_path):
    service = SimpleNamespace(policy=SimpleNamespace(base_dir=tmp_path),
                              close=AsyncMock(), start=lambda: None, wait_ready=AsyncMock())
    return ConnectManager(service)


def run(coroutine):
    return asyncio.run(coroutine)


def test_only_device_code_is_exposed(manager):
    manager.authorization_line('  https://accounts.t3.codes/device?user_code=ABCD-EFGH\n')
    manager.authorization_line('access_token=secret\n')
    assert manager.code is None
    manager.authorization_line('Confirm this code when asked: ABCD-EFGH\n')
    manager.authorization_line('Waiting for approval (expires in 10 min). Press Ctrl+C to cancel.\n')
    assert manager.snapshot()['verification_url'] == 'https://accounts.t3.codes/device'
    assert manager.code == 'ABCD-EFGH'
    assert manager.expires_at > time.time()
    assert 'secret' not in json.dumps(manager.snapshot())
    manager.set_state('connecting', 'Connecting')
    assert manager.code is manager.expires_at is None


def test_link_waits_for_idle_then_restarts_and_checks_route(manager, monkeypatch):
    async def scenario():
        states = iter([{'relayClient': {'status': 'available'}}, {'desired': True, 'authenticated': True, 'linked': False}])
        manager.saved_status = AsyncMock(side_effect=lambda: next(states))
        manager._process = AsyncMock(return_value=b'')
        checks = iter([True, False])
        manager.active_chats = lambda: next(checks)
        async def sleep(_):
            assert manager.state == 'waiting_idle'
            manager.service.close.assert_not_awaited()
        monkeypatch.setattr('neurodesk_t3_code.connect.asyncio.sleep', sleep)
        manager._wait_reachable = AsyncMock()
        await manager._link()
        manager.service.close.assert_awaited_once()
        manager.service.wait_ready.assert_awaited_once()
        manager._wait_reachable.assert_awaited_once()
    run(scenario())


def test_retry_saved_link_does_not_restart(manager):
    async def scenario():
        manager.saved_status = AsyncMock(return_value={'relayClient': {'status': 'available'}, 'desired': True, 'authenticated': True, 'linked': True})
        manager._process = AsyncMock(return_value=b'')
        manager._wait_reachable = AsyncMock()
        await manager._link()
        manager.service.close.assert_not_awaited()
        manager._wait_reachable.assert_awaited_once()
    run(scenario())


def test_restore_checks_reachability_without_reauthorizing(manager):
    async def scenario():
        manager.saved_status = AsyncMock(return_value={'desired': True, 'authenticated': True})
        manager._wait_reachable = AsyncMock()
        manager._process = AsyncMock()
        await manager.restore()
        await manager.task
        manager._wait_reachable.assert_awaited_once()
        manager._process.assert_not_awaited()
    run(scenario())


def test_busy_actions_and_cancellation(manager):
    async def scenario():
        stopped = asyncio.Event()
        async def link():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        manager._link = link
        await manager.action('link')
        await asyncio.sleep(0)
        with pytest.raises(HTTPError) as error:
            await manager.action('link')
        assert error.value.status_code == 409
        await manager.action('cancel')
        assert stopped.is_set()
        assert manager.state == 'idle'
        with pytest.raises(HTTPError):
            await manager.action('shell')
    run(scenario())


def test_errors_expiry_and_success_clear_code(manager):
    async def scenario():
        manager.code = 'ABCD-EFGH'
        await manager._run(AsyncMock(side_effect=asyncio.TimeoutError()))
        assert manager.state == 'expired' and manager.code is None
        await manager._run(AsyncMock(side_effect=ValueError('secret-token')))
        assert manager.state == 'error' and 'secret-token' not in manager.message
    run(scenario())


def test_route_delay_is_not_reported_as_ready(manager, monkeypatch):
    async def scenario():
        manager.reachable = AsyncMock(side_effect=[False, True])
        async def sleep(_):
            assert manager.state == 'connecting'
        monkeypatch.setattr('neurodesk_t3_code.connect.asyncio.sleep', sleep)
        await manager._wait_reachable()
        assert manager.state == 'ready'
        assert manager.reachable.await_count == 2
    run(scenario())


def test_route_timeout_retains_saved_link(manager, monkeypatch):
    monkeypatch.setattr('neurodesk_t3_code.connect.ROUTE_TIMEOUT', 0)
    with pytest.raises(ConnectError, match='link is saved'):
        run(manager._wait_reachable())
    manager.service.close.assert_not_awaited()


def test_active_chat_check_fails_closed(manager):
    assert manager.active_chats()
    path = manager.service.policy.base_dir / 'userdata'
    path.mkdir()
    with sqlite3.connect(path / 'state.sqlite') as db:
        db.execute('CREATE TABLE projection_thread_sessions(status TEXT, active_turn_id TEXT)')
        db.execute("INSERT INTO projection_thread_sessions VALUES ('ready', NULL)")
    assert not manager.active_chats()
    with sqlite3.connect(path / 'state.sqlite') as db:
        db.execute("UPDATE projection_thread_sessions SET active_turn_id='turn-1'")
    assert manager.active_chats()


def test_readiness_never_sends_oauth_token_to_environment(manager):
    state = manager.service.policy.base_dir / 'userdata'
    (state / 'secrets').mkdir(parents=True)
    (state / 'environment-id').write_text('env-1')
    (state / 'secrets/cloud-cli-oauth-token.bin').write_text(json.dumps({'accessToken':'private', 'expiresAtEpochMs':time.time()*1000+60000}))
    async def scenario():
        manager._json = AsyncMock(side_effect=[{'environments': [{'environmentId':'env-1','label':'Neurodesk','endpoint':{'httpBaseUrl':'https://example.t3.codes/'}}]}, {'authenticated':False}])
        assert await manager.reachable()
        assert manager._json.call_args_list[0].kwargs == {'token':'private'}
        assert manager._json.call_args_list[1].kwargs == {}
        assert manager.label == 'Neurodesk'
        assert 'private' not in json.dumps(manager.snapshot())
    run(scenario())


def test_disconnect_requires_verified_removal(manager):
    async def scenario():
        manager._process = AsyncMock(return_value=b'')
        manager.saved_status = AsyncMock(return_value={'desired':True, 'linked':True})
        with pytest.raises(ConnectError, match='not completed'):
            await manager._unlink()
        manager.saved_status.return_value = {'desired':False, 'linked':False}
        await manager._unlink()
        assert manager.state == 'idle'
    run(scenario())


def test_device_cli_is_bounded_and_killed_on_cancel(manager, tmp_path, monkeypatch):
    import os
    executable = tmp_path / 't3'
    executable.write_text('#!/usr/bin/env python3\nimport time\nprint("Confirm this code when asked: ABCD-EFGH", flush=True)\ntime.sleep(60)\n')
    executable.chmod(0o755)
    manager.service.policy.executable = executable
    manager.service.policy.workdir = tmp_path
    manager.service.environ = os.environ.copy()
    monkeypatch.setattr('neurodesk_t3_code.connect.server_environment', lambda p, e: e)
    async def scenario():
        ready = asyncio.Event()
        def on_line(line):
            manager.authorization_line(line)
            ready.set()
        task = asyncio.create_task(manager._process('link', '--headless', timeout=10, on_line=on_line))
        await asyncio.wait_for(ready.wait(), 5)
        assert manager.code == 'ABCD-EFGH'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(asyncio.TimeoutError):
            await manager._process('link', '--headless', timeout=0.05)
    run(scenario())


def test_cancel_during_restart_finishes_restart(manager):
    async def scenario():
        manager.saved_status = AsyncMock(return_value={'relayClient':{'status':'available'}, 'desired':True,'authenticated':True,'linked':False})
        manager._process = AsyncMock(return_value=b'')
        manager.active_chats = lambda: False
        closing = asyncio.Event()
        release = asyncio.Event()
        async def close():
            closing.set()
            await release.wait()
        manager.service.close = close
        await manager.action('link')
        await closing.wait()
        cancelling = asyncio.create_task(manager.action('cancel'))
        await asyncio.sleep(0)
        release.set()
        await cancelling
        manager.service.wait_ready.assert_awaited_once()
    run(scenario())
