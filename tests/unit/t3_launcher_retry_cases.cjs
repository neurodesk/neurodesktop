const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/typescript');
const { JSDOM } = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/jsdom');
const dom = new JSDOM('', { url: 'https://notebook.test/user/alice/lab' });
const compiled = ts.transpileModule(fs.readFileSync(process.argv[2], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText;

let state = 'parked';
let retryOk = true;
let accept = true;
const requests = [];
const dialogs = [];
const panels = [];
const json = body => ({ ok: true, json: async () => body });
const Dialog = {
  okButton: (options = {}) => ({ accept: true, label: options.label ?? 'OK' }),
  cancelButton: () => ({ accept: false, label: 'Cancel' })
};
const modules = {
  '@jupyterlab/application': {},
  '@jupyterlab/apputils': {
    Dialog,
    showDialog: async options => {
      dialogs.push(options);
      const retry = options.buttons.find(button => button.label === 'Retry SciGent');
      return { button: retry && accept ? retry : options.buttons[0] };
    },
    MainAreaWidget: class {
      constructor({ content }) { this.content = content; this.title = {}; this.isDisposed = false; }
    }
  },
  '@jupyterlab/coreutils': { URLExt: { join: (...parts) => parts.join('/').replace(/\/+/g, '/') } },
  '@jupyterlab/docmanager': { IDocumentManager: {} },
  '@jupyterlab/launcher': { ILauncher: {} },
  '@jupyterlab/services': { ServerConnection: {
    makeSettings: () => ({ baseUrl: '/user/alice/' }),
    makeRequest: async (url, init) => {
      requests.push([url, init.method ?? 'GET']);
      if (url.endsWith('neurodesk-t3-status')) return json({ state });
      if (url.endsWith('neurodesk-t3-retry')) {
        if (retryOk) state = 'starting';
        return retryOk ? json({ state }) : { ok: false };
      }
      return { ok: true };
    }
  } },
  '@jupyterlab/ui-components': { codeIcon: {} },
  '@lumino/widgets': { Widget: class { constructor({ node }) { this.node = node; this.disposed = { connect: () => {} }; } } },
  './workspaceLinks': { bindWorkspaceLinkFrame: () => () => {} },
  './t3Connect': { createConnectPanel: () => ({}) }
};
const exported = {};
new Function('exports', 'require', 'window', 'document', compiled)(
  exported, name => {
    assert.ok(Object.hasOwn(modules, name), name);
    return modules[name];
  }, dom.window, dom.window.document);

const commands = new Map();
const launcherItems = [];
exported.default.activate(
  {
    commands: { addCommand: (id, spec) => commands.set(id, spec) },
    shell: { add: panel => panels.push(panel), activateById: () => {} }
  },
  { add: item => launcherItems.push(item) },
  {}
);
const open = () => commands.get('neurodesk-launcher:open-t3-code').execute();
const reset = () => { requests.length = 0; dialogs.length = 0; };

(async () => {
  assert.deepEqual(launcherItems.map(item => item.command), ['neurodesk-launcher:open-t3-code']);

  accept = false;
  await open();
  assert.equal(dialogs.length, 1);
  assert.match(dialogs[0].body, /kernels and terminals keep running/);
  assert.deepEqual(dialogs[0].buttons.map(button => button.label), ['Cancel', 'Retry SciGent']);
  assert.deepEqual(requests, [['/user/alice/neurodesk-t3-status', 'GET']], 'cancel must not restart');

  reset();
  accept = true;
  await open();
  assert.deepEqual(requests, [
    ['/user/alice/neurodesk-t3-status', 'GET'],
    ['/user/alice/neurodesk-t3-retry', 'POST']
  ]);
  assert.match(dialogs.at(-1).body, /SciGent is restarting/);
  assert.equal(panels.length, 0, 'a restarting SciGent opens no iframe');

  reset();
  await open();
  assert.equal(dialogs.length, 1);
  assert.match(dialogs[0].body, /still starting/);
  assert.ok(!dialogs[0].buttons.some(button => button.label === 'Retry SciGent'),
    'a starting SciGent is not offered a restart');
  assert.equal(requests.length, 1);

  reset();
  state = 'parked';
  retryOk = false;
  await open();
  assert.match(dialogs.at(-1).body, /Could not restart SciGent/);

  reset();
  state = 'ready';
  await open();
  assert.equal(dialogs.length, 0);
  assert.equal(panels.length, 1);
  assert.equal(panels[0].title.label, 'scigent.ai');
})().catch(error => {
  console.error(error);
  process.exit(1);
});
