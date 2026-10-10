#!/usr/bin/env python3
"""Execute and verify a notebook using authenticated Jupyter kernel/Contents APIs."""
import argparse
import json
import os
from pathlib import Path
import struct
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

import requests
import websocket


class AcceptanceError(Exception):
    pass


class Jupyter:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.query:
            raise AcceptanceError('server URL must be credential-free HTTP(S)')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise AcceptanceError('remote Jupyter requires HTTPS')
        self.url = url.rstrip('/') + '/'
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'token ' + token
        self.token = token

    def request(self, method, path, body=None):
        try:
            response = self.session.request(method, self.url + path, json=body, timeout=60, allow_redirects=False)
        except requests.RequestException:
            raise AcceptanceError('Jupyter HTTP transport failed') from None
        if method == 'DELETE' and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            raise AcceptanceError(f'Jupyter {method} request failed with HTTP {response.status_code}')
        if response.content:
            try:
                return response.json()
            except ValueError:
                raise AcceptanceError('Jupyter returned invalid JSON') from None

    def execute(self, notebook, timeout):
        kernel = self.request('POST', 'api/kernels', {'name': notebook['metadata']['kernelspec']['name']})
        kernel_path = 'api/kernels/' + quote(kernel['id'], safe='')
        connection = None
        try:
            parsed = urlsplit(self.url + kernel_path + '/channels')
            ws_url = urlunsplit(parsed._replace(scheme='wss' if parsed.scheme == 'https' else 'ws'))
            try:
                connection = websocket.create_connection(ws_url,
                    header={'Authorization': 'token ' + self.token}, timeout=timeout, redirect_limit=0,
                    subprotocols=['v1.kernel.websocket.jupyter.org'])
            except websocket.WebSocketException:
                connection = websocket.create_connection(ws_url,
                    header={'Authorization': 'token ' + self.token}, timeout=timeout, redirect_limit=0)
            protocol = connection.getsubprotocol()
            for index, cell in enumerate(notebook['cells'], 1):
                if cell['cell_type'] != 'code':
                    continue
                message_id = uuid.uuid4().hex
                request = {'header': {'msg_id': message_id, 'username': 'acceptance',
                    'session': uuid.uuid4().hex, 'msg_type': 'execute_request', 'version': '5.3'},
                    'parent_header': {}, 'metadata': {}, 'channel': 'shell',
                    'content': {'code': ''.join(cell['source']), 'silent': False,
                        'store_history': True, 'user_expressions': {}, 'allow_stdin': False,
                        'stop_on_error': True}}
                if protocol == 'v1.kernel.websocket.jupyter.org':
                    parts = [b'shell'] + [json.dumps(request[key]).encode()
                        for key in ('header', 'parent_header', 'metadata', 'content')]
                    offsets = [8 * (len(parts) + 2)]
                    for part in parts:
                        offsets.append(offsets[-1] + len(part))
                    frame = b''.join([len(offsets).to_bytes(8, 'little'),
                        *[offset.to_bytes(8, 'little') for offset in offsets], *parts])
                    connection.send_binary(frame)
                else:
                    connection.send(json.dumps(request))
                cell['outputs'] = []
                reply = None
                idle = False
                deadline = time.monotonic() + timeout
                while reply is None or not idle:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AcceptanceError(f'cell {index} timed out')
                    connection.settimeout(remaining)
                    frame = connection.recv()
                    if protocol == 'v1.kernel.websocket.jupyter.org':
                        parts = int.from_bytes(frame[:8], 'little')
                        if not 6 <= parts < len(frame) // 8:
                            raise ValueError('invalid binary kernel frame')
                        offsets = [int.from_bytes(frame[8 * (i + 1):8 * (i + 2)], 'little')
                            for i in range(parts)]
                        if offsets[0] != 8 * (parts + 1) or offsets[-1] != len(frame) or offsets != sorted(offsets):
                            raise ValueError('invalid binary kernel offsets')
                        message = {'channel': frame[offsets[0]:offsets[1]].decode()}
                        for i, key in enumerate(('header', 'parent_header', 'metadata', 'content'), 1):
                            message[key] = json.loads(frame[offsets[i]:offsets[i + 1]])
                    else:
                        if isinstance(frame, bytes):
                            if len(frame) < 8:
                                raise ValueError('invalid binary kernel frame')
                            parts = struct.unpack('!I', frame[:4])[0]
                            if not 1 <= parts < len(frame) // 4:
                                raise ValueError('invalid binary kernel frame')
                            start = struct.unpack('!I', frame[4:8])[0]
                            end = struct.unpack('!I', frame[8:12])[0] if parts > 1 else len(frame)
                            if not 4 * (parts + 1) <= start <= end <= len(frame):
                                raise ValueError('invalid binary kernel offsets')
                            frame = frame[start:end]
                        message = json.loads(frame)
                    if message.get('parent_header', {}).get('msg_id') != message_id:
                        continue
                    kind = message['header']['msg_type']
                    content = message['content']
                    if kind == 'execute_reply' and message.get('channel') == 'shell':
                        reply = content
                        cell['execution_count'] = content.get('execution_count')
                    elif kind == 'status' and content['execution_state'] == 'idle':
                        idle = True
                    elif kind in ('stream', 'display_data', 'execute_result', 'error'):
                        output = {'output_type': kind, **content}
                        if kind in ('display_data', 'execute_result'):
                            output.pop('transient', None)
                        cell['outputs'].append(output)
                    elif kind == 'clear_output':
                        cell['outputs'] = []
                if reply['status'] != 'ok' or any(o['output_type'] == 'error' for o in cell['outputs']):
                    raise AcceptanceError(f'cell {index} failed')
        except (websocket.WebSocketException, ValueError, KeyError, TypeError,
                IndexError, AttributeError, struct.error) as error:
            detail = str(error) if str(error) in ('invalid binary kernel frame', 'invalid binary kernel offsets') else type(error).__name__
            raise AcceptanceError(f'Jupyter kernel protocol failed ({detail})') from None
        finally:
            if connection is not None:
                connection.close()
            failing = sys.exc_info()[0] is not None
            try:
                self.request('DELETE', kernel_path)
            except AcceptanceError:
                if not failing:
                    raise


def validate_notebook(notebook, expected):
    cells = notebook.get('cells', [])
    if len(cells) != len(expected['cells']):
        raise AcceptanceError('saved notebook cell count differs')
    counts = []
    for index, (cell, original) in enumerate(zip(cells, expected['cells'], strict=True), 1):
        if cell.get('cell_type') != original['cell_type'] or ''.join(cell.get('source', [])) != ''.join(original['source']):
            raise AcceptanceError(f'saved notebook cell {index} differs')
        if cell['cell_type'] == 'code':
            count = cell.get('execution_count')
            if type(count) is not int or count < 1:
                raise AcceptanceError(f'saved notebook cell {index} was not executed')
            if any(output.get('output_type') == 'error' for output in cell.get('outputs', [])):
                raise AcceptanceError(f'saved notebook cell {index} contains an error')
            if count != original.get('execution_count') or cell.get('outputs', []) != original.get('outputs', []):
                raise AcceptanceError(f'saved notebook cell {index} execution result differs')
            counts.append(count)
    if not counts or counts != sorted(set(counts)):
        raise AcceptanceError('saved notebook execution counts are invalid')


def run(url, token, notebook, output_name, timeout, evidence):
    client = Jupyter(url, token)
    path = 'api/contents/' + quote(output_name, safe='')
    try:
        response = client.session.delete(client.url + path, timeout=60, allow_redirects=False)
        if response.status_code not in (204, 404):
            raise AcceptanceError('could not remove previous notebook')
        client.execute(notebook, timeout)
        expected = json.loads(json.dumps(notebook))
        client.request('PUT', path, {'type': 'notebook', 'format': 'json', 'content': notebook})
        saved = client.request('GET', path)['content']
        validate_notebook(saved, expected)
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'executed.ipynb').write_text(json.dumps(saved, indent=2))
        print('Notebook executed and verified through the Contents API')
    finally:
        failing = sys.exc_info()[0] is not None
        try:
            client.request('DELETE', path)
        except AcceptanceError:
            if not failing:
                raise
        finally:
            client.session.close()


def science_notebook():
    sources = [
        'import module\nawait module.load("fsl/6.0.7.16")',
        'import tempfile, pathlib, urllib.request, shutil, subprocess\n'
        'work = pathlib.Path(tempfile.mkdtemp(prefix="neurodesktop-bet-"))\n'
        'archive = work / "preCourse.tar.gz"\n'
        'with urllib.request.urlopen("https://fsl.fmrib.ox.ac.uk/fslcourse/downloads/preCourse.tar.gz", timeout=120) as response, archive.open("wb") as target:\n'
        '    shutil.copyfileobj(response, target)\n'
        'subprocess.run(["tar", "-xzf", str(archive), "-C", str(work)], check=True, timeout=120)\n'
        'image = work / "fsl_course_data/intro/structural.nii.gz"\n'
        'assert image.is_file(), "course structural image missing"',
        'brain = work / "structural_brain.nii.gz"\n'
        'mask = work / "structural_brain_mask.nii.gz"\n'
        'subprocess.run(["bet", str(image), str(brain), "-m", "-s"], check=True, timeout=900)',
        Path(__file__).with_name('validate_fsl_bet.py').read_text().split('\ndef main():')[0]
        + '\nprint(json.dumps(validate(image, brain, mask), sort_keys=True))',
        'shutil.rmtree(work)',
    ]
    return {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'kernelspec': {
        'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}},
        'cells': [{'cell_type': 'code', 'id': f'bet-{index}', 'metadata': {},
            'source': source, 'execution_count': None, 'outputs': []}
            for index, source in enumerate(sources)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-url')
    parser.add_argument('--validate-notebook', type=Path)
    parser.add_argument('--expected-notebook', type=Path)
    parser.add_argument('--notebook', type=Path)
    parser.add_argument('--output-name', default='FSL_course_bet_test_output.ipynb')
    parser.add_argument('--timeout', type=float, default=1200)
    parser.add_argument('--evidence-dir', type=Path, default=Path('fsl-evidence'))
    args = parser.parse_args()
    try:
        if args.validate_notebook:
            if not args.expected_notebook:
                raise AcceptanceError('--expected-notebook is required')
            validate_notebook(json.loads(args.validate_notebook.read_text()),
                json.loads(args.expected_notebook.read_text()))
            return 0
        if not args.server_url:
            raise AcceptanceError('--server-url is required')
        token = os.environ.get('USER_TOKEN', '')
        if not token:
            raise AcceptanceError('USER_TOKEN is required')
        notebook = json.loads(args.notebook.read_text()) if args.notebook else science_notebook()
        run(args.server_url, token, notebook, args.output_name,
            args.timeout, args.evidence_dir)
    except (AcceptanceError, requests.RequestException):
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, AcceptanceError) else 'Jupyter transport failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
