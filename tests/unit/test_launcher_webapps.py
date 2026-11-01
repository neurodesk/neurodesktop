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
dom.window.open = (...args) => { opened.push(args); return null; };
const modules = {
  './workspaceLinks': {}, './astraViewer': {}, './t3Code': {},
  './webappsAppearance': {},
  '@jupyterlab/apputils': {}, '@jupyterlab/launcher': { ILauncher: {} },
  '@jupyterlab/ui-components': { notebookIcon: { name: 'ui-components:notebook' } },
  '@jupyterlab/coreutils': {
    PageConfig: { getOption: () => 'true' },
    URLExt: { join: (...parts) => parts.join('/') }
  },
  '@jupyterlab/services': { ServerConnection: {
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
const app = { commands: { addCommand: (id, options) => commands.set(id, options) } };
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


def test_getting_started_tile_opens_the_tutorial_in_a_new_tab():
    run_main_plugin_case(r'''
  const entries = await activateLauncher();
  const neurodesk = entries.filter(entry => entry.category === 'Neurodesk');
  const tile = neurodesk.find(entry => commands.get(entry.command)?.label === 'Getting started');
  assert.ok(tile, 'Getting started tile must be in the Neurodesk category');
  assert.ok(neurodesk.every(entry => entry === tile || entry.rank > tile.rank),
    'Getting started must precede the built-in Neurodesk tools');
  const command = commands.get(tile.command);
  assert.equal(command.icon.name, 'ui-components:notebook');
  assert.equal(opened.length, 0, 'activation must not open a tab');
  command.execute();
  assert.deepEqual(opened, [[
    'https://neurodesk.org/edu/tutorials/about-neurodesk/accessing-neurodesk-tools',
    '_blank', 'noopener'
  ]]);
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
