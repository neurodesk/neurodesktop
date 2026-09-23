const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/typescript');
const { JSDOM } = require(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES + '/jsdom');
const dom = new JSDOM('', { url: 'https://notebook.test/user/alice/lab' });
const compiled = ts.transpileModule(fs.readFileSync(process.argv[2], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText;
const PAGE = 'https://app.t3.codes/settings/general';
let served;
class Widget {
  constructor({ node }) { this.node = node; }
}
const modules = {
  '@jupyterlab/application': {},
  '@jupyterlab/apputils': { MainAreaWidget: class {
    constructor({ content }) {
      this.content = content;
      this.title = {};
      this.isDisposed = false;
      this.handlers = [];
      this.disposed = { connect: handler => this.handlers.push(handler) };
    }
    dispose() { this.isDisposed = true; for (const handler of this.handlers) handler(); }
  } },
  '@jupyterlab/coreutils': { URLExt: { join: (...parts) => parts.join('/') } },
  '@jupyterlab/services': { ServerConnection: {
    makeSettings: () => ({ baseUrl: '/user/alice/' }),
    makeRequest: async () => served
  } },
  '@lumino/widgets': { Widget }
};
const exported = {};
new Function('exports', 'require', 'window', 'document', 'setTimeout', 'clearTimeout', compiled)(
  exported, name => {
    assert.ok(Object.hasOwn(modules, name), name);
    return modules[name];
  }, dom.window, dom.window.document, dom.window.setTimeout, dom.window.clearTimeout);

const shell = { add: () => {}, activateById: () => {} };
async function panelFor(response) {
  served = response;
  const panel = exported.createConnectPanel({ shell });
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  const [authorize, connections] = [...panel.content.node.querySelectorAll('a')];
  panel.dispose();
  return { node: panel.content.node, authorize, connections };
}
function status(extra) {
  return { ok: true, json: async () => ({
    state: 'error', message: 'The link is saved, but the relay is not reachable yet.',
    code: null, verification_url: null, expires_at: null, label: null, linked: true,
    connections_url: null, ...extra
  }) };
}
(async () => {
  const limited = await panelFor(status({ connections_url: PAGE }));
  assert.equal(limited.authorize.href, 'https://accounts.t3.codes/device',
    'the device-code link stays first, so the second link is the connections link');
  assert.ok(limited.connections, 'the connections link must exist in the panel');
  assert.equal(limited.connections.hidden, false, 'a tunnel failure must show the connections link');
  assert.equal(limited.connections.href, PAGE);
  assert.equal(limited.connections.textContent, `Manage your T3 connections at ${PAGE}`);
  assert.match(limited.node.querySelector('[role="status"]').textContent,
    /relay is not reachable yet/, 'the failure itself stays in the status line');

  const ready = await panelFor({ ok: true, json: async () => ({
    state: 'ready', message: 'Ready.', code: null, verification_url: null,
    expires_at: null, label: 'alice@hub', linked: true, connections_url: null
  }) });
  assert.equal(ready.connections.hidden, true, 'a reachable tunnel must not show the link');

  for (const forged of ['javascript:alert(1)', 'https://evil.test/settings/general', PAGE + '/../..']) {
    const hostile = await panelFor(status({ connections_url: forged }));
    assert.equal(hostile.connections.hidden, true, forged);
    assert.equal(hostile.connections.href, PAGE, forged);
  }

  const injected = await panelFor(status({
    message: '<img src=x onerror=alert(1)> see https://evil.test',
    connections_url: PAGE
  }));
  assert.equal(injected.node.querySelector('img'), null, 'server text must never become markup');
  assert.equal(injected.node.querySelectorAll('a').length, 2, 'server text must not add links');

  const unreachable = await panelFor({ ok: false, status: 500 });
  assert.equal(unreachable.connections.hidden, true, 'a Jupyter outage is not a tunnel limit');
  console.log('ok');
})();
