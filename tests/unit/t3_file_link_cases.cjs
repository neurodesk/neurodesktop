const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/typescript');
const { JSDOM } = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/jsdom');
const dom = new JSDOM('<iframe></iframe>', { url: 'https://notebook.test/user/alice/lab' });
const compiled = ts.transpileModule(fs.readFileSync(process.argv[2], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText;
const errors = [], opened = [], commands = [];
const modules = {
  '@jupyterlab/application': {},
  '@jupyterlab/apputils': { showErrorMessage: (...args) => errors.push(args) },
  '@jupyterlab/coreutils': { PageConfig: { getOption: () => '/home/alice' } },
  '@jupyterlab/docmanager': { IDocumentManager: {} }
};
const exported = {};
new Function('exports', 'require', 'window', compiled)(exported, name => {
  assert.ok(Object.hasOwn(modules, name));
  return modules[name];
}, dom.window);
const frame = dom.window.document.querySelector('iframe');
const dispose = exported.bindWorkspaceLinkFrame(frame,
  { commands: { execute: async (...args) => commands.push(args) } },
  {
    services: { contents: { get: async path => {
      if (path === 'missing.nii.gz') throw new Error('Not found');
      return { type: path === 'outputs' ? 'directory' : 'file' };
    } } },
    registry: { getWidgetFactory: () => { throw new Error('Must use default handler'); } },
    openOrReveal: (...args) => opened.push(args)
  }
);
let previews = 0;
function populate() {
  frame.contentDocument.body.innerHTML = '<main><a><span>file</span></a></main>';
  frame.contentDocument.querySelector('main').addEventListener('click', () => previews++);
}
async function click(href, options = {}, download = false) {
  const anchor = frame.contentDocument.querySelector('a');
  anchor.href = href;
  anchor.toggleAttribute('download', download);
  // Block browser navigation after recording whether our capture handler claimed it.
  let claimed;
  anchor.addEventListener('click', event => { claimed = event.defaultPrevented; event.preventDefault(); }, { once: true });
  const event = new frame.contentWindow.MouseEvent('click', { bubbles: true, cancelable: true, ...options });
  anchor.querySelector('span').dispatchEvent(event);
  await new Promise(resolve => setImmediate(resolve));
  return claimed === undefined; // Claimed capture never reaches the anchor.
}
(async () => {
  frame.dispatchEvent(new dom.window.Event('load'));
  populate();
  for (const name of ['mask.nii.gz', 'report.md', 'analysis.ipynb', 'qc.png', 'a%20file.json']) {
    assert.equal(await click('https://notebook.test/home/alice/' + name), true);
    assert.deepEqual(opened.at(-1), [decodeURIComponent(name), 'default']);
  }
  assert.equal(previews, 0, 'T3 preview must not receive claimed clicks');
  await click('https://notebook.test/home/alice/outputs');
  assert.equal(commands[0][0], 'filebrowser:go-to-path');
  assert.equal(commands[0][1].path, 'outputs');
  const count = opened.length;
  for (const [url, options, download] of [
    ['https://external.test/home/alice/mask.nii.gz'],
    ['https://notebook.test/etc/passwd'],
    ['https://notebook.test/home/alice-other/mask.nii.gz'],
    ['https://notebook.test/home/alice/%2e%2e%2fsecret'],
    ['https://notebook.test/home/alice/%ZZ'],
    ...['ctrlKey', 'metaKey', 'shiftKey', 'altKey'].map(key => ['https://notebook.test/home/alice/mask.nii.gz', { [key]: true }]),
    ['https://notebook.test/home/alice/mask.nii.gz', { button: 1 }],
    ['https://notebook.test/home/alice/mask.nii.gz', {}, true]
  ]) assert.equal(await click(url, options, download), false, url);
  assert.equal(opened.length, count);
  await click('https://notebook.test/home/alice/missing.nii.gz');
  assert.equal(errors.length, 1);
  assert.equal(opened.length, count);
  // Reload creates a new document; old listeners must be removed.
  const oldAnchor = frame.contentDocument.querySelector('a');
  oldAnchor.href = 'https://notebook.test/home/alice/old.nii.gz';
  frame.src = 'about:blank';
  await new Promise(resolve => frame.addEventListener('load', resolve, { once: true }));
  populate();
  await click('https://notebook.test/home/alice/reloaded.nii.gz');
  assert.deepEqual(opened.at(-1), ['reloaded.nii.gz', 'default']);
  const afterReload = opened.length;
  oldAnchor.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(opened.length, afterReload);
  // Repeated load notifications cannot multiply handlers.
  frame.dispatchEvent(new dom.window.Event('load'));
  await click('https://notebook.test/home/alice/once.nii.gz');
  assert.equal(opened.length, afterReload + 1);
  // An unrelated document and an inaccessible cross-origin frame are not bound.
  const outside = dom.window.document.createElement('a');
  outside.href = 'https://notebook.test/home/alice/outside.nii.gz';
  dom.window.document.body.append(outside);
  outside.addEventListener('click', event => event.preventDefault());
  outside.click();
  assert.equal(opened.length, afterReload + 1);
  Object.defineProperty(frame, 'contentDocument', { configurable: true, value: null });
  frame.dispatchEvent(new dom.window.Event('load'));
  delete frame.contentDocument;
  assert.equal(await click('https://notebook.test/home/alice/unbound.nii.gz'), false);
  frame.dispatchEvent(new dom.window.Event('load'));
  dispose();
  frame.dispatchEvent(new dom.window.Event('load'));
  assert.equal(await click('https://notebook.test/home/alice/disposed.nii.gz'), false);
  assert.equal(opened.length, afterReload + 1);
  dom.window.close();
})().catch(error => { console.error(error); process.exit(1); });
