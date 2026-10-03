import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'scripts/check_fsl_notebook.py'

@pytest.fixture(scope='module', params=['modern', 'legacy'])
def server(tmp_path_factory, request):
    directory = tmp_path_factory.mktemp('fsl-jupyter')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = 'local-acceptance-secret'
    data = directory / 'jupyter-data' / 'kernels' / 'python3'
    data.mkdir(parents=True)
    (data / 'kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m',
        'ipykernel_launcher', '-f', '{connection_file}'], 'display_name': 'Python 3',
        'language': 'python'}))
    process = subprocess.Popen([
        sys.executable, '-m', 'jupyter_server', '--no-browser', '--allow-root',
        f'--ServerApp.port={port}', '--ServerApp.port_retries=0',
        '--ServerApp.base_url=/acceptance/', f'--ServerApp.root_dir={directory}',
        f'--IdentityProvider.token={token}',
        *(['--ZMQChannelsWebsocketConnection.kernel_ws_protocol=']
          if request.param == 'legacy' else []),
    ], env={**os.environ, 'JUPYTER_DATA_DIR': str(directory / 'jupyter-data')},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}/acceptance/'
    try:
        for _ in range(200):
            if process.poll() is not None:
                pytest.fail('local Jupyter exited during startup')
            try:
                if requests.get(url + 'api', headers={'Authorization': f'token {token}'}, timeout=.2).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            pytest.fail('local Jupyter did not become ready')
        yield url, token, directory
    finally:
        process.terminate()
        process.wait(timeout=15)


def run_notebook(server, tmp_path, source):
    notebook = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'kernelspec': {
        'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}},
        'cells': [{'cell_type': 'code', 'id': 'acceptance', 'metadata': {},
                   'source': source, 'execution_count': None, 'outputs': []}]}
    fixture = tmp_path / 'input.json'
    fixture.write_text(json.dumps(notebook))
    url, token, _ = server
    env = {**os.environ, 'USER_TOKEN': token}
    return subprocess.run([sys.executable, str(CLI), '--server-url', url,
                           '--notebook', str(fixture), '--timeout', '20',
                           '--evidence-dir', str(tmp_path / 'evidence')],
                          env=env, capture_output=True, text=True, timeout=40)


def test_failed_command_cannot_pass_by_printing_success(server, tmp_path):
    result = run_notebook(server, tmp_path,
        "import subprocess\nprint('All steps completed structural_brain')\n"
        "subprocess.run(['sh', '-c', 'echo success; exit 7'], check=True)")
    assert result.returncode != 0
    assert 'local-acceptance-secret' not in result.stdout + result.stderr
    assert 'cell 1 failed' in result.stderr


def test_success_is_saved_and_retrieved_with_real_execution(server, tmp_path):
    result = run_notebook(server, tmp_path, "print('computed', 6 * 7)")
    assert result.returncode == 0, result.stderr
    saved = json.loads((tmp_path / 'evidence/executed.ipynb').read_text())
    assert saved['cells'][0]['execution_count'] == 1
    assert saved['cells'][0]['outputs'][0]['text'] == 'computed 42\n'
    url, token, _ = server
    assert requests.get(url + 'api/kernels', headers={'Authorization': 'token ' + token}).json() == []


def test_unexecuted_notebook_with_success_in_source_fails(tmp_path):
    fixture = tmp_path / 'source-only.ipynb'
    fixture.write_text(json.dumps({'cells': [{'cell_type': 'code',
        'source': "print('All steps completed structural_brain')", 'execution_count': None,
        'outputs': []}]}))
    result = subprocess.run([sys.executable, str(CLI), '--validate-notebook', str(fixture),
        '--expected-notebook', str(fixture)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'was not executed' in result.stderr


@pytest.mark.parametrize(('corruption', 'error'), [
    (None, None),
    ('error-output', 'saved notebook cell 2 contains an error'),
    ('repeated-count', 'saved notebook cell 2 execution result differs'),
    ('changed-source', 'saved notebook cell 2 differs'),
])
def test_saved_notebook_preserves_executed_cells(tmp_path, corruption, error):
    expected = {'cells': [{'cell_type': 'code', 'source': 'print(42)',
        'execution_count': 1, 'outputs': [{'output_type': 'stream', 'name': 'stdout',
                                         'text': '42\n'}]},
        {'cell_type': 'code', 'source': 'print(43)', 'execution_count': 2,
         'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': '43\n'}]}]}
    saved = json.loads(json.dumps(expected))
    if corruption == 'error-output':
        saved['cells'][1]['outputs'] = [{'output_type': 'error', 'ename': 'CalledProcessError',
            'evalue': 'failed', 'traceback': []}]
    elif corruption == 'repeated-count':
        saved['cells'][1]['execution_count'] = 1
    elif corruption == 'changed-source':
        saved['cells'][1]['source'] = "print('All steps completed')"
    original = tmp_path / 'expected.ipynb'
    artifact = tmp_path / 'saved.ipynb'
    original.write_text(json.dumps(expected))
    artifact.write_text(json.dumps(saved))
    result = subprocess.run([sys.executable, str(CLI), '--validate-notebook', str(artifact),
        '--expected-notebook', str(original)], capture_output=True, text=True)
    assert result.returncode == (1 if error else 0), result.stderr
    if error:
        assert result.stderr.strip() == error


def test_stdout_before_timeout_does_not_establish_completion(server, tmp_path):
    notebook = tmp_path / 'sleep.ipynb'
    notebook.write_text(json.dumps({'metadata': {'kernelspec': {'name': 'python3'}},
        'cells': [{'cell_type': 'code', 'source': "import time\nprint('All steps completed')\ntime.sleep(10)",
                   'execution_count': None, 'outputs': []}]}))
    url, token, _ = server
    result = subprocess.run([sys.executable, str(CLI), '--server-url', url,
        '--notebook', str(notebook), '--timeout', '2', '--evidence-dir', str(tmp_path / 'evidence')],
        env={**os.environ, 'USER_TOKEN': token}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert not (tmp_path / 'evidence/executed.ipynb').exists()


def test_binary_widget_traffic_does_not_break_notebook_execution(server, tmp_path):
    result = run_notebook(server, tmp_path,
        "kernel = get_ipython().kernel\n"
        "kernel.session.send(kernel.iopub_socket, 'comm_msg', "
        "content={'comm_id': 'acceptance', 'data': {}}, "
        "parent=kernel.get_parent(), buffers=[b'\\xff\\x00'])\nprint(42)")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('corruption', ['erased-outputs', 'changed-output', 'changed-count'])
def test_saved_results_must_match_the_executed_notebook(tmp_path, corruption):
    expected = {'cells': [{'cell_type': 'code', 'source': 'print(6 * 7)',
        'execution_count': 1, 'outputs': [{'output_type': 'stream', 'name': 'stdout',
                                          'text': '42\n'}]}]}
    saved = json.loads(json.dumps(expected))
    if corruption == 'erased-outputs':
        saved['cells'][0]['outputs'] = []
    elif corruption == 'changed-output':
        saved['cells'][0]['outputs'][0]['text'] = '99\n'
    else:
        saved['cells'][0]['execution_count'] = 2
    original = tmp_path / 'executed.ipynb'
    artifact = tmp_path / 'saved.ipynb'
    original.write_text(json.dumps(expected))
    artifact.write_text(json.dumps(saved))
    result = subprocess.run([sys.executable, str(CLI), '--validate-notebook', str(artifact),
        '--expected-notebook', str(original)], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'execution result differs' in result.stderr
