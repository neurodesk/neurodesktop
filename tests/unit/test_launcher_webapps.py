"""Run the production launcher appearance helper against a real DOM."""
import os
import shutil
import subprocess

import pytest
from testlib import repo_path


def test_webapps_catalog_order_and_heading_survive_rerender():
    modules = os.environ.get('NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES')
    if not modules or not shutil.which('node'):
        pytest.skip('Set NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES to jsdom/typescript modules')
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
