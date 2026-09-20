import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';

// Execute the complete source module. Only its Jupyter/DOM dependencies are
// substituted; the exported functions and installed click handler are real.
const errors = [];
const listeners = [];
const context = vm.createContext({
  URL,
  document: {
    baseURI: 'https://notebook.test/user/alice/lab',
    addEventListener: (...args) => listeners.push(args)
  },
  window: { location: { origin: 'https://notebook.test' } }
});
const subject = new vm.SourceTextModule(
  stripTypeScriptTypes(readFileSync(process.argv[2], 'utf8')),
  { context }
);
const imports = {
  '@jupyterlab/application': { JupyterFrontEnd: {}, JupyterFrontEndPlugin: {} },
  '@jupyterlab/apputils': { showErrorMessage: (...args) => errors.push(args) },
  '@jupyterlab/coreutils': { PageConfig: { getOption: () => '/home/alice' } },
  '@jupyterlab/docmanager': { IDocumentManager: {} }
};
await subject.link(specifier => {
  assert.ok(Object.hasOwn(imports, specifier), `Unexpected dependency: ${specifier}`);
  const exports = imports[specifier];
  return new vm.SyntheticModule(Object.keys(exports), function () {
    for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
  }, { context });
});
await subject.evaluate();
const { toWorkspaceRelativePath, stripLineReference, renderedFactoryFor, shouldClaimClick } = subject.namespace;

await test('workspace mapping accepts only paths within the root', () => {
  for (const [path, root, expected] of [
    ['/home/alice/report.md', '/home/alice', 'report.md'],
    ['/home/alice/sub/report.md', '/home/alice/', 'sub/report.md'],
    ['/home/alice/sub/', '/home/alice', 'sub'],
    ['/home/alice', '/home/alice', ''],
    ['/home/alice-other/report.md', '/home/alice', null],
    ['/home/alice/../secret', '/home/alice', null],
    ['/lab/tree/report.md', '/home/alice', null],
    ['relative.md', '/home/alice', null],
    ['/home/alice/report.md', '', null],
    ['/etc/passwd', '/', null]
  ]) assert.equal(toWorkspaceRelativePath(path, root), expected, `${path} under ${root}`);
});

await test('line references and rendered factories handle real filenames', () => {
  for (const [path, expected] of [
    ['report.md:42', 'report.md'], ['sub/code.py:12:3', 'sub/code.py'],
    [':1', null], ['report.md', null], ['report.md:abc', null],
    ['file:part.md:7', 'file:part.md']
  ]) assert.equal(stripLineReference(path), expected, path);
  for (const [path, expected] of [
    ['report.md', 'Markdown Preview'], ['sub/report.MARKDOWN', 'Markdown Preview'],
    ['report.html', 'HTML Viewer'], ['report.HTM', 'HTML Viewer'],
    ['.md', null], ['README', null], ['report.txt', null], ['dir.md/file', null]
  ]) assert.equal(renderedFactoryFor(path), expected, path);
});

const unmodified = { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
await test('modified, secondary and previously handled clicks stay with the browser', () => {
  assert.equal(shouldClaimClick(unmodified), true);
  for (const key of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey', 'defaultPrevented']) {
    assert.equal(shouldClaimClick({ ...unmodified, [key]: true }), false, key);
  }
  for (const button of [1, 2]) assert.equal(shouldClaimClick({ ...unmodified, button }), false);
});

const stats = [], opened = [], commands = [];
let models = new Map(), factories = new Set(['Markdown Preview', 'HTML Viewer']);
subject.namespace.default.activate(
  { commands: { execute: async (...args) => commands.push(args) } },
  {
    services: { contents: { get: async path => {
      stats.push(path);
      if (!models.has(path)) throw new Error('Not found');
      return { type: models.get(path) };
    } } },
    registry: { getWidgetFactory: name => factories.has(name) },
    openOrReveal: async (...args) => opened.push(args)
  }
);
assert.equal(listeners.length, 1);
const [eventName, click, capture] = listeners[0];
assert.equal(eventName, 'click');
assert.equal(capture, true);

async function dispatch(href, { download = false, ...overrides } = {}) {
  stats.length = opened.length = commands.length = errors.length = 0;
  let prevented = false, stopped = false;
  click({ ...unmodified, ...overrides,
    target: { closest: () => ({ href, hasAttribute: name => name === 'download' && download }) },
    preventDefault: () => { prevented = true; },
    stopPropagation: () => { stopped = true; }
  });
  await new Promise(resolve => setImmediate(resolve));
  return { prevented, stopped };
}

await test('clicking an encoded workspace link opens its rendered document', async () => {
  models = new Map([['a report.md', 'file']]);
  assert.deepEqual(await dispatch('/home/alice/a%20report.md'), { prevented: true, stopped: true });
  assert.deepEqual(opened, [['a report.md', 'Markdown Preview']]);
  assert.deepEqual(errors, []);
});

await test('external links, downloads and malformed URLs are not intercepted', async () => {
  for (const [url, options] of [
    ['https://external.test/home/alice/a.md', {}], ['/lab', {}],
    ['/home/alice/%2E%2E%2Fsecret', {}], ['/home/alice/%ZZ', {}],
    ['/home/alice/a.md', { download: true }], ['/home/alice/a.md', { ctrlKey: true }]
  ]) {
    assert.deepEqual(await dispatch(url, options), { prevented: false, stopped: false }, url);
    assert.deepEqual(stats, []);
    assert.deepEqual(opened, []);
  }
});

await test('literal colon filenames win before line-reference fallback', async () => {
  models = new Map([['report.md:1', 'file'], ['report.md', 'file']]);
  await dispatch('/home/alice/report.md:1');
  assert.deepEqual(stats, ['report.md:1']);
  assert.deepEqual(opened, [['report.md:1', 'default']]);
  models.delete('report.md:1');
  await dispatch('/home/alice/report.md:1');
  assert.deepEqual(stats, ['report.md:1', 'report.md']);
  assert.deepEqual(opened, [['report.md', 'Markdown Preview']]);
});

await test('directories reveal and missing viewers use the default factory', async () => {
  models = new Map([['sub', 'directory'], ['report.html', 'file']]);
  await dispatch('/home/alice/sub');
  assert.equal(commands[0][0], 'filebrowser:go-to-path');
  assert.equal(commands[0][1].path, 'sub');
  assert.deepEqual(opened, []);
  factories = new Set();
  await dispatch('/home/alice/report.html');
  assert.deepEqual(opened, [['report.html', 'default']]);
});

await test('an intercepted missing file reports the original requested path', async () => {
  models = new Map();
  assert.equal((await dispatch('/home/alice/missing.md:7')).prevented, true);
  assert.deepEqual(opened, []);
  assert.equal(errors.length, 1);
  assert.equal(errors[0][0], 'Cannot open file');
  assert.match(errors[0][1], /missing\.md:7 could not be opened/);
});
