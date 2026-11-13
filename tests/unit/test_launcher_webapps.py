"""Run the production launcher appearance helper against a real DOM."""
import os
import shutil
import subprocess

import pytest
from testlib import repo_path


MAIN_PLUGIN_HARNESS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/typescript');
const { JSDOM } = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/jsdom');
const dom = new JSDOM('');
const opened = [];
const notifications = [];
dom.window.open = (...args) => { opened.push(args); return null; };
const modules = {
  './workspaceLinks': {}, './astraViewer': {}, './t3Code': {},
  './webappsAppearance': {},
  '@jupyterlab/apputils': { Notification: { error: message => notifications.push(message) } },
  '@jupyterlab/launcher': { ILauncher: {} },
  '@jupyterlab/ui-components': { notebookIcon: { name: 'ui-components:notebook' } },
  '@jupyterlab/coreutils': {
    PageConfig: { getOption: () => 'true' },
    URLExt: { join: (...parts) => parts.join('/') }
  },
  '@jupyterlab/services': { ServerConnection: {
    ResponseError: class extends Error {
      constructor(response) { super(`HTTP ${response.status}`); this.response = response; }
    },
    makeSettings: () => ({ baseUrl: '/user/test/' }),
    makeRequest: async () => ({ ok: true, json: async () => ({ server_processes: [] }) })
  } }
};
const compiled = ts.transpileModule(fs.readFileSync(process.argv[1], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText;
const exported = {};
new Function('exports', 'require', 'window', 'document', 'MutationObserver',
  'requestAnimationFrame', compiled)(exported, name => {
    assert.ok(Object.hasOwn(modules, name), name);
    return modules[name];
  }, dom.window, dom.window.document, dom.window.MutationObserver, () => 1);
const commands = new Map();
const executed = [];
const files = new Map();
const contents = {
  get: async path => {
    if (!files.has(path)) {
      throw new modules['@jupyterlab/services'].ServerConnection.ResponseError({ status: 404 });
    }
    return files.get(path);
  },
  save: async (path, model) => { files.set(path, model); return model; }
};
const app = {
  commands: {
    addCommand: (id, options) => commands.set(id, options),
    execute: async (id, args) => { executed.push([id, args]); }
  },
  serviceManager: { contents }
};
const NOTEBOOK_URL = 'https://raw.githubusercontent.com/neurodesk/neurodeskedu/main/'
  + 'books/tutorials/about_neurodesk/accessing_neurodesk_tools.ipynb';
const NOTEBOOK_PATH = 'neurodesk-getting-started/accessing_neurodesk_tools.ipynb';
const fetched = [];
function serveNotebook(response) {
  globalThis.fetch = async url => { fetched.push(url); return response; };
}
function gettingStartedCommand(entries) {
  const tile = entries.find(entry => commands.get(entry.command)?.label === 'Getting started');
  return commands.get(tile.command);
}
async function activateLauncher() {
  const entries = [];
  await exported.default.find(p => p.id === 'neurodesk-launcher:plugin')
    .activate(app, { add: entry => entries.push(entry) });
  return entries;
}
'''


def run_main_plugin_case(case):
    modules = os.environ.get('NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES')
    if not modules or not shutil.which('node'):
        pytest.fail('Set NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES as described in docs/testing.md.')
    script = MAIN_PLUGIN_HARNESS + "(async () => {" + case + "\n})().catch(error => { console.error(error); process.exitCode = 1; })" \
        ".finally(() => dom.window.close());"
    subprocess.run(['node', '-e', script, str(repo_path(
        'extensions/neurodesk-launcher/src/index.ts'))], check=True, timeout=30)


def test_slurm_tile_uses_the_upstream_command():
    run_main_plugin_case(r'''
  // Contract from the pinned NERSC extension: it registers no slurm:open alias.
  let slurmOpened = 0;
  commands.set('jupyterlab-slurm:open', {
    label: 'Slurm Dashboard', iconClass: 'jp-SlurmWidget-NerscLaunchIcon',
    execute: () => slurmOpened++
  });
  const entries = await activateLauncher();
  const tile = entries.find(entry => entry.category === 'Neurodesk' && entry.rank === 4);
  assert.ok(tile, 'Slurm tile must remain in the Neurodesk category');
  const command = commands.get(tile.command);
  assert.ok(command, `Command '${tile.command}' not registered`);
  assert.equal(command.label, 'Slurm Dashboard');
  assert.equal(command.iconClass, 'jp-SlurmWidget-NerscLaunchIcon');
  command.execute();
  assert.equal(slurmOpened, 1);
''')


def test_getting_started_tile_copies_the_tutorial_notebook_into_the_workspace():
    run_main_plugin_case(r'''
  const notebook = { cells: [], metadata: {}, nbformat: 4, nbformat_minor: 5 };
  serveNotebook({ ok: true, status: 200, json: async () => notebook });
  const entries = await activateLauncher();
  const neurodesk = entries.filter(entry => entry.category === 'Neurodesk');
  const tile = neurodesk.find(entry => commands.get(entry.command)?.label === 'Getting started');
  assert.ok(tile, 'Getting started tile must be in the Neurodesk category');
  assert.ok(neurodesk.every(entry => entry === tile || entry.rank > tile.rank),
    'Getting started must precede the built-in Neurodesk tools');
  const command = commands.get(tile.command);
  assert.equal(command.icon.name, 'ui-components:notebook');
  assert.equal(fetched.length, 0, 'activation must not download the tutorial');
  await command.execute();
  assert.deepEqual(fetched, [NOTEBOOK_URL]);
  assert.deepEqual(files.get('neurodesk-getting-started'), { type: 'directory' });
  assert.deepEqual(files.get(NOTEBOOK_PATH),
    { type: 'notebook', format: 'json', content: notebook });
  assert.deepEqual(executed, [['docmanager:open', { path: NOTEBOOK_PATH }]]);
  assert.equal(opened.length, 0, 'the tutorial must open inside JupyterLab, not a browser tab');
''')


def test_getting_started_tile_reopens_an_existing_copy_without_overwriting_it():
    run_main_plugin_case(r'''
  const progress = { type: 'notebook', content: 'user edits' };
  files.set(NOTEBOOK_PATH, progress);
  serveNotebook({ ok: true, status: 200, json: async () => ({}) });
  await gettingStartedCommand(await activateLauncher()).execute();
  assert.equal(fetched.length, 0, 'an existing copy must not be downloaded again');
  assert.equal(files.get(NOTEBOOK_PATH), progress);
  assert.deepEqual(executed, [['docmanager:open', { path: NOTEBOOK_PATH }]]);
''')


def test_getting_started_tile_reports_a_failed_download():
    run_main_plugin_case(r'''
  serveNotebook({ ok: false, status: 503, json: async () => ({}) });
  const originalError = console.error;
  console.error = () => {};
  const command = gettingStartedCommand(await activateLauncher());
  await command.execute().finally(() => { console.error = originalError; });
  assert.equal(files.size, 0, 'a failed download must not create files');
  assert.equal(executed.length, 0);
  assert.equal(notifications.length, 1);
  assert.match(notifications[0], /getting started notebook.*503/);
''')


def test_getting_started_tile_survives_an_unavailable_server_proxy():
    run_main_plugin_case(r'''
  modules['@jupyterlab/services'].ServerConnection.makeRequest = async () => ({ ok: false, status: 503 });
  const originalError = console.error;
  console.error = () => {};
  const entries = await activateLauncher().finally(() => { console.error = originalError; });
  assert.ok(entries.some(entry => commands.get(entry.command)?.label === 'Getting started'),
    'Getting started must not depend on servers-info');
''')


def test_webapps_catalog_order_and_heading_survive_rerender():
    modules = os.environ.get('NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES')
    if not modules or not shutil.which('node'):
        pytest.fail('Launcher DOM tests require Node.js and jsdom/typescript. '
                    'Set NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES as described in docs/testing.md.')
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/typescript');
const { JSDOM } = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/jsdom');
const compiled = ts.transpileModule(fs.readFileSync(process.argv[1], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText;
const exported = {};
new Function('exports', compiled)(exported);
const apply = exported.applyWebappsAppearance;
const dom = new JSDOM(`<section><div class="jp-Launcher-sectionHeader"><svg width="32" height="32" data-icon="ezbids"><path d="old"/></svg><h2>Webapps</h2></div><div class="jp-Launcher-cardContainer"></div></section>`);
const section = dom.window.document.querySelector('section');
const container = section.querySelector('.jp-Launcher-cardContainer');
for (const [name, id] of [['ezBIDS','ezbids'], ['jamovi','jamovi'], ['More webapps','more-webapps'], ['OpenRefine','openrefine'], ['RStudio Server','rstudio']]) {
  container.insertAdjacentHTML('beforeend', `<div class="jp-LauncherCard"><div class="jp-LauncherCard-icon"><svg data-icon="neurodesk-launcher:${id}"><path d="${id}"/></svg></div><div class="jp-LauncherCard-label">${name}</div></div>`);
}
apply(section);
const visualOrder = Array.from(container.children).sort((a,b) => Number(a.style.order)-Number(b.style.order)).map(el=>el.textContent);
assert.deepEqual(visualOrder, ['ezBIDS','jamovi','OpenRefine','RStudio Server','More webapps']);
let heading = section.querySelector('.jp-Launcher-sectionHeader svg');
assert.equal(heading.dataset.icon, 'neurodesk-launcher:more-webapps');
assert.equal(heading.querySelector('path').getAttribute('d'), 'more-webapps');
assert.equal(heading.getAttribute('width'), '32');
apply(section);
assert.equal(section.querySelector('.jp-Launcher-sectionHeader svg'), heading, 'No repeated mutation/observer loop');
heading.outerHTML = '<svg data-icon="ezbids"><path d="rerender"/></svg>';
apply(section);
assert.equal(section.querySelector('.jp-Launcher-sectionHeader svg').dataset.icon, 'neurodesk-launcher:more-webapps');
// No catalog yet: leave the native heading in place until its tile arrives.
container.querySelector('[data-icon="neurodesk-launcher:more-webapps"]').closest('.jp-LauncherCard').remove();
heading = section.querySelector('.jp-Launcher-sectionHeader svg');
apply(section);
assert.equal(section.querySelector('.jp-Launcher-sectionHeader svg'), heading);
'''
    subprocess.run(['node', '-e', script, str(repo_path(
        'extensions/neurodesk-launcher/src/webappsAppearance.ts'))], check=True, timeout=30)
