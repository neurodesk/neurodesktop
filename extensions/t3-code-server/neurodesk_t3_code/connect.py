"""Jupyter-owned device authorization for the pinned T3 CLI.

CLI output is untrusted and never returned wholesale. OAuth credentials stay in
T3's private store; only a bounded device code is exposed to the signed-in user.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import ipaddress
import os
import re
import signal
import socket
import sqlite3
import time
from urllib.parse import urlsplit, urlencode

from tornado import httpclient, web
from tornado.netutil import OverrideResolver, Resolver
from tornado.simple_httpclient import SimpleAsyncHTTPClient
from jupyter_server.base.handlers import APIHandler

from .supervisor import server_environment

RELAY = 'https://relay.t3.codes'
DEVICE_PAGE = 'https://accounts.t3.codes/device'
CONNECTIONS_PAGE = 'https://app.t3.codes/settings/general'
DEFAULT_TUNNEL_LIMIT = 3
AUTH_TIMEOUT = 660
ROUTE_TIMEOUT = 240


class ConnectError(Exception):
    """A deliberately credential-free user-facing failure."""

    def __init__(self, message, *, connections_url=None):
        super().__init__(message)
        self.connections_url = connections_url


class RoutePending(ConnectError):
    """The approved tunnel has not finished routing yet."""


async def fetch_tunnel_with_public_dns(url):
    """Resolve only T3's public tunnel through DoH; preserve URL/SNI/TLS checks."""
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/api/auth/session'
            or not re.fullmatch(r'[a-z0-9-]+\.t3coderelay\.com', host)):
        raise RoutePending('The connection hostname cannot be resolved.')
    dns_url = 'https://cloudflare-dns.com/dns-query?' + urlencode({'name': host, 'type': 'A'})
    # No OAuth or Jupyter credentials go to the DNS service or public tunnel.
    client = SimpleAsyncHTTPClient(force_instance=True, max_body_size=1024 * 1024)
    try:
        response = await client.fetch(dns_url, headers={'Accept': 'application/dns-json'},
                                      follow_redirects=False, request_timeout=10)
    finally:
        client.close()
    data = json.loads(response.body)
    addresses = []
    if data.get('Status') == 0:
        for answer in data.get('Answer', []):
            if answer.get('type') != 1:
                continue
            address = ipaddress.ip_address(answer['data'])
            if address.version == 4 and address.is_global and str(address) not in addresses:
                addresses.append(str(address))
    for address in addresses[:3]:
        resolver = OverrideResolver(resolver=Resolver(), mapping={host: address})
        client = SimpleAsyncHTTPClient(force_instance=True, resolver=resolver,
                                       max_body_size=1024 * 1024)
        try:
            return await client.fetch(
                url, headers={'Accept': 'application/json', 'User-Agent': 'Neurodesk-T3/0.1.0'},
                follow_redirects=False, request_timeout=10, raise_error=False)
        except (OSError, httpclient.HTTPClientError):
            continue
        finally:
            client.close()
            resolver.close()
    raise RoutePending('The public relay hostname is not reachable yet.')


class ConnectManager:
    def __init__(self, service):
        self.service = service
        self.state = 'idle'
        self.message = 'Use T3 here without linking, or connect it to your desktop app.'
        self.code = None
        self.expires_at = None
        self.label = None
        self.linked = False
        self.connections_url = None
        self.last_checked = 0
        self.task = None
        self.lock = asyncio.Lock()
        self._name_pending = False

    async def configure_name(self, host):
        from .naming import environment_label, remember_public_host
        environ = self.service.environ
        before = environment_label(self.service.policy, environ)
        remember_public_host(self.service.policy, environ, host)
        self._name_pending |= before != environment_label(self.service.policy, environ)
        async with self.lock:
            if self._name_pending and (not self.task or self.task.done()) and not self.active_chats():
                await self._restart()

    async def _restart(self):
        async def restart():
            await self.service.close()
            self.service.start()
            await self.service.wait_ready()
        restarting = asyncio.create_task(restart())
        try:
            await asyncio.shield(restarting)
        except asyncio.CancelledError:
            # A cancelled browser request must not strand the sidecar stopped.
            await restarting
            raise
        self._name_pending = False

    def snapshot(self):
        return dict(state=self.state, message=self.message, code=self.code,
                    verification_url=DEVICE_PAGE if self.code else None,
                    expires_at=self.expires_at, label=self.label, linked=self.linked,
                    connections_url=self.connections_url, last_checked=self.last_checked)

    def set_state(self, state, message):
        self.state, self.message = state, message
        self.connections_url = None
        if state != 'authorizing':
            self.code = self.expires_at = None

    async def _process(self, *args, timeout=30, on_line=None):
        policy = self.service.policy
        process = await asyncio.create_subprocess_exec(
            str(policy.executable), 'connect', *args, '--base-dir', str(policy.base_dir),
            env={**server_environment(policy, self.service.environ), "NO_COLOR": "1"}, cwd=policy.workdir,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True, limit=65536,
        )
        output = bytearray()
        try:
            async with asyncio.timeout(timeout):
                async for line in process.stdout:
                    output.extend(line)
                    if len(output) > 65536:
                        raise ConnectError('T3 returned an unexpected response. Please retry.')
                    if on_line:
                        on_line(line.decode('utf-8', errors='replace'))
                if await process.wait():
                    raise ConnectError('T3 Connect could not complete the request. Check your connection and retry.')
            return bytes(output)
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()

    async def saved_status(self):
        value = json.loads(await self._process('status', '--json'))
        if not isinstance(value, dict):
            raise ConnectError('Could not read T3 Connect status.')
        self.linked = bool(value.get("desired") or value.get("linked"))
        return value

    def authorization_line(self, line):
        match = re.fullmatch(r'Confirm this code when asked: ([A-Z0-9]{4}-[A-Z0-9]{4})\s*', line)
        if match:
            self.set_state('authorizing', 'Approve this code using the same account as your T3 desktop app.')
            self.code = match[1]
            self.expires_at = time.time() + 600
        expiry = re.search(r'Waiting for approval \(expires in (\d+) min\)', line)
        if expiry and self.code:
            self.expires_at = time.time() + min(int(expiry[1]) * 60, AUTH_TIMEOUT)

    def active_chats(self):
        """Fail closed when the pinned projection cannot establish restart safety."""
        path = self.service.policy.base_dir / 'userdata/state.sqlite'
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                return db.execute(
                    'SELECT count(*) FROM projection_thread_sessions '
                    "WHERE active_turn_id IS NOT NULL OR status IN ('running', 'starting')"
                ).fetchone()[0] != 0
        except sqlite3.Error:
            return True

    async def _json(self, url, *, token=None):
        headers = {'Accept': 'application/json', 'User-Agent': 'Neurodesk-T3/0.1.0'}
        if token:
            # Credentials are only ever sent to the fixed official relay.
            if url != RELAY + '/v1/environments':
                raise ConnectError('Unexpected relay address.')
            headers['Authorization'] = 'Bearer ' + token
        try:
            response = await httpclient.AsyncHTTPClient().fetch(
                url, headers=headers, follow_redirects=False, request_timeout=10,
                raise_error=False,
            )
        except socket.gaierror:
            # Only the credential-free T3 tunnel probe may use public DNS.
            if token:
                raise
            response = await fetch_tunnel_with_public_dns(url)

        if token and response.code in (401, 403):
            raise ConnectError('Your T3 authorization needs refreshing. Choose Retry to sign in again.')
        if response.code not in (200, 401) or len(response.body) > 1024 * 1024:
            raise RoutePending("The connection is not ready yet.")
        return json.loads(response.body)

    async def reachable(self):
        """Resolve our endpoint at the relay, then probe it without credentials.

        A successful CLI link or a running cloudflared process is not readiness.
        The pinned T3 CLI owns token refresh; this method never refreshes or writes
        its credentials and never returns them to the browser.
        """
        state = self.service.policy.base_dir / 'userdata'
        token = json.loads((state / 'secrets/cloud-cli-oauth-token.bin').read_text())
        environment_id = (state / 'environment-id').read_text().strip()
        if token['expiresAtEpochMs'] <= time.time() * 1000:
            raise ConnectError('Your T3 authorization needs refreshing. Choose Retry to sign in again.')
        records = await self._json(RELAY + '/v1/environments', token=token['accessToken'])
        for record in records['environments']:
            if record['environmentId'] != environment_id:
                continue
            endpoint = record['endpoint']['httpBaseUrl']
            url = urlsplit(endpoint)
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or url.port not in (None, 443) or url.query or url.fragment):
                raise ConnectError('The relay returned an unsupported endpoint.')
            result = await self._json(endpoint.rstrip('/') + '/api/auth/session')
            # An unauthenticated session response proves routing reaches T3 without
            # sending a Jupyter cookie or minting another application credential.
            if isinstance(result.get('authenticated'), bool):
                self.label = str(record['label'])[:200]
                return True
        return False

    async def _wait_reachable(self):
        deadline = asyncio.get_running_loop().time() + ROUTE_TIMEOUT
        self.set_state('connecting', 'Starting the connection. This can take a few minutes.')
        dns_failed = False
        while asyncio.get_running_loop().time() < deadline:
            limit = getattr(self.service, 'connect_tunnel_limit', None)
            if limit is not None:
                raise ConnectError(
                    f'This T3 account has reached its limit of {limit} managed tunnels. '
                    'Disconnect an unused environment from T3 Connect, or ask T3 support '
                    'to increase the account limit, then choose Retry. '
                    'Your sign-in is saved; signing in again will not free a tunnel.',
                    connections_url=CONNECTIONS_PAGE,
                )
            if self._name_pending and not self.active_chats():
                await self._restart()
            dns_failed = False
            try:
                if await self.reachable():
                    self.last_checked = time.time()
                    self.set_state('ready', 'Ready. In T3 desktop, open Settings → Connections and add this environment.')
                    return
            except socket.gaierror:
                dns_failed = True
                self.set_state('connecting', 'The link is saved, but Neurodesktop cannot resolve the relay hostname. Retrying DNS…')
            except (OSError, ValueError, KeyError, TypeError, RoutePending, httpclient.HTTPClientError):
                pass
            await asyncio.sleep(3)
        if dns_failed:
            raise ConnectError('The link is saved, but Neurodesktop cannot resolve the relay hostname. Ask your administrator to check cluster DNS. The connection may already work from your T3 app.')
        raise ConnectError(
            'The link is saved, but the relay is not reachable yet. Retry checks the saved '
            'link and refreshes authorization if needed. A T3 account allows only '
            f'{DEFAULT_TUNNEL_LIMIT} connected environments by default, so an account that is '
            'already full cannot add this one.',
            connections_url=CONNECTIONS_PAGE,
        )

    async def _link(self):
        saved = await self.saved_status()
        if saved.get('relayClient', {}).get('status') != 'available':
            raise ConnectError('The relay client is missing from this image. Ask your administrator to update Neurodesktop.')
        self.set_state('starting', 'Preparing secure sign-in…')
        await self._process('link', '--headless', timeout=AUTH_TIMEOUT,
                            on_line=self.authorization_line)
        saved = await self.saved_status()
        if not saved.get('desired') or not saved.get('authenticated'):
            raise ConnectError('Authorization did not complete. Please retry.')
        if not saved.get('linked') or self._name_pending:
            self.set_state('waiting_idle', 'Authorization saved. Waiting for active chats to finish before restarting T3.')
            deadline = time.monotonic() + 1800
            while self.active_chats():
                if time.monotonic() > deadline:
                    raise ConnectError('T3 is still busy, or its chat status is unavailable. Finish active chats and choose Retry.')
                await asyncio.sleep(3)
            self.set_state('restarting', 'Restarting T3 to activate your link. Jupyter stays open.')
            await self._restart()
        await self._wait_reachable()

    async def restore(self):
        async def check():
            saved = await self.saved_status()
            if saved.get('desired') and saved.get('authenticated'):
                await self._wait_reachable()
        async with self.lock:
            if self.task is None:
                self.task = asyncio.create_task(self._run(check))

    async def _unlink(self):
        self.set_state('disconnecting', 'Disconnecting this environment…')
        await self._process('unlink')
        saved = await self.saved_status()
        if saved.get('desired') or saved.get('linked'):
            raise ConnectError('Disconnect has not completed. Retry Disconnect when the relay is reachable.')
        self.label = None
        self.set_state('idle', 'Disconnected. You can continue using T3 in Jupyter.')

    async def _run(self, operation):
        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.set_state('expired', 'The request timed out or the code expired. Choose Retry for a new attempt.')
        except ConnectError as error:
            self.set_state('error', str(error))
            self.connections_url = error.connections_url
        except Exception:
            # Never expose subprocess output, HTTP errors with headers, or secrets.
            self.set_state('error', 'Could not complete T3 Connect setup. Please retry.')

    async def action(self, action):
        async with self.lock:
            if action == 'cancel':
                await self.close()
                with suppress(Exception):
                    await self.saved_status()
                self.set_state('idle', 'Setup cancelled. An already approved link is kept; Disconnect revokes it.')
            elif action in ('link', 'retry', 'disconnect'):
                if self.task and not self.task.done():
                    raise web.HTTPError(409, reason='T3 Connect is already processing a request.')
                self.set_state('starting', 'Checking T3 Connect…')
                self.task = asyncio.create_task(self._run(self._unlink if action == 'disconnect' else self._link))
            else:
                raise web.HTTPError(400, reason='Unknown T3 Connect action.')
        return self.snapshot()

    async def refresh(self):
        if self.state == 'ready' and time.time() - self.last_checked > 30:
            async with self.lock:
                if not self.task or self.task.done():
                    self.task = asyncio.create_task(self._run(self._wait_reachable))

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.task = None


class T3ConnectHandler(APIHandler):
    def initialize(self, t3_app):
        self.t3_app = t3_app

    def manager(self):
        if self.t3_app._connect is None:
            raise web.HTTPError(503, reason='T3 is not available.')
        self.set_header('Cache-Control', 'no-store')
        return self.t3_app._connect

    @web.authenticated
    async def get(self):
        manager = self.manager()
        await manager.refresh()
        self.finish(manager.snapshot())

    @web.authenticated
    async def post(self):
        body = self.get_json_body() or {}
        if (not isinstance(body, dict) or set(body) != {'action'}
                or not isinstance(body['action'], str)):
            raise web.HTTPError(400)
        self.finish(await self.manager().action(body['action']))
