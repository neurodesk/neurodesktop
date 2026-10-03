import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

const require = createRequire(join(process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES, 'package.json'));
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><body></body>');
globalThis.document = dom.window.document;
const { render } = await import(`data:text/javascript;base64,${Buffer.from(readFileSync(process.argv[2])).toString('base64')}`);

class Model {
  constructor(graph) {
    this.values = { graph, mode: 'flow', expanded: [], selected_node: null };
    this.listeners = new Map();
  }
  get(key) { return this.values[key]; }
  set(key, value) {
    this.values[key] = value;
    for (const callback of this.listeners.get(`change:${key}`) || []) callback();
  }
  on(event, callback) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(callback);
  }
  off(event, callback) {
    if (callback) this.listeners.get(event)?.delete(callback);
    else this.listeners.delete(event);
  }
  save_changes() {}
}

function graph(overrides = {}) {
  return {
    meta: { analysis_name: 'Brain extraction' }, warnings: [], errors: [], gaps: [], nodes: [],
    trust: { label: 'Declared', level: 'declared', message: 'Nothing here was executed.' },
    projection: {
      nodes: [
        { id: 'input', label: 'Scan', kind: 'input', rank: 0, order: 0 },
        { id: 'stage', label: 'Extract brain', kind: 'stage', rank: 8, order: 0 },
        { id: 'output', label: 'Mask', kind: 'output', rank: 20, order: 0, evidence_rank: 9, evidence_order: 1 },
        { id: 'cluster', label: 'Extraction options', kind: 'decision-cluster', rank: 8, order: 1, target: 'stage' },
        { id: 'decision', label: 'Threshold', kind: 'decision', parent: 'cluster' },
        { id: 'finding', label: 'Mask quality', kind: 'finding', rank: 40, order: 0, evidence_rank: 9, evidence_order: 0 },
        { id: 'insight', label: 'Prior comparison', kind: 'insight', evidence_rank: 2, evidence_order: 0 },
      ],
      edges: [
        { source: 'input', target: 'stage', kind: 'flow' },
        { source: 'stage', target: 'output', kind: 'produces' },
        { source: 'cluster', target: 'stage', kind: 'configures' },
        { source: 'decision', target: 'stage', kind: 'configures', parent: 'cluster' },
        { source: 'output', target: 'finding', kind: 'supports' },
        { source: 'insight', target: 'finding', kind: 'informs' },
      ],
    }, ...overrides,
  };
}
function mount(value = graph(), model = new Model(value)) {
  const el = document.createElement('section');
  document.body.append(el);
  return { el, model, cleanup: render({ model, el }) };
}
const node = (el, label) => el.querySelector(`g[aria-label="${label}"]`);
const labels = el => [...el.querySelectorAll('g[aria-label]')].map(item => item.getAttribute('aria-label')).sort();
const click = el => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
const mode = (el, name) => click([...el.querySelectorAll('nav button')].find(item => item.textContent === name));
const position = (el, label) => node(el, label).getAttribute('transform').match(/[\d.]+/g).map(Number);

const cases = {
  warnings() {
    const { el } = mount(graph({ warnings: ['Older schema detected'], errors: ['Stage is invalid'] }));
    const warning = el.querySelector('.astra-warnings');
    const error = el.querySelector('.astra-errors');
    assert.ok(warning, 'Schema warnings remain visible on validation failure');
    assert.ok(error, 'Validation errors are displayed');
    assert.match(warning.textContent, /Older schema detected/);
    assert.match(error.textContent, /Stage is invalid/);
    assert.ok(warning.compareDocumentPosition(error) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.equal(el.querySelector('svg'), null);
  },
  layout() {
    const { el } = mount();
    const input = position(el, 'Scan');
    const stage = position(el, 'Extract brain');
    const output = position(el, 'Mask');
    assert.ok(input[1] < stage[1] && stage[1] < output[1]);
    assert.equal(stage[1] - input[1], output[1] - stage[1], 'Sparse ranks compact into consecutive rows');
    mode(el, 'Evidence');
    const insight = position(el, 'Prior comparison');
    const finding = position(el, 'Mask quality');
    const mask = position(el, 'Mask');
    assert.ok(insight[1] < finding[1]);
    assert.equal(finding[1], mask[1]);
    assert.ok(finding[0] < mask[0], 'Evidence order determines left-to-right placement');
    mode(el, 'Decisions');
    assert.equal(node(el, 'Threshold'), null);
    click(node(el, 'Extraction options'));
    assert.ok(node(el, 'Threshold'));
    assert.ok(el.querySelector('.astra-cluster-box'));
    click([...el.querySelectorAll('button')].find(item => item.textContent === 'Collapse decisions'));
    assert.equal(node(el, 'Threshold'), null);
    assert.equal(el.querySelector('.astra-cluster-box'), null);
  },
  scroll() {
    const { el, model } = mount();
    dom.window.SVGElement.prototype.focus = function (options) {
      if (!options?.preventScroll) {
        const viewport = this.closest('.astra-viewport');
        viewport.scrollTop = viewport.scrollLeft = 0;
      }
    };
    function offset() {
      const viewport = el.querySelector('.astra-viewport');
      viewport.scrollTop = 260;
      viewport.scrollLeft = 85;
    }
    function retained() {
      const viewport = el.querySelector('.astra-viewport');
      assert.equal(viewport.scrollTop, 260);
      assert.equal(viewport.scrollLeft, 85);
    }
    offset();
    mode(el, 'Decisions');
    retained();
    click(node(el, 'Extraction options'));
    retained();
    const mask = node(el, 'Mask');
    mask.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    retained();
    assert.equal(model.get('selected_node'), 'output');
    assert.ok(node(el, 'Mask').classList.contains('is-selected'));
  },
  evidence() {
    const { el } = mount();
    mode(el, 'Evidence');
    assert.deepEqual(labels(el), ['Mask', 'Mask quality', 'Prior comparison']);
    assert.equal(el.querySelectorAll('.astra-edge').length, 2);
    assert.equal(el.querySelector('.astra-notice').hidden, true);
    const empty = mount(graph({ projection: { nodes: [
      { id: 'stage', label: 'Extract brain', kind: 'stage', rank: 0, order: 0 },
    ], edges: [] } })).el;
    mode(empty, 'Evidence');
    assert.deepEqual(labels(empty), []);
    assert.equal(empty.querySelector('.astra-notice').hidden, false);
    assert.match(empty.querySelector('.astra-notice').textContent, /nothing for the Evidence view to show/);
    mode(empty, 'Flow');
    assert.deepEqual(labels(empty), ['Extract brain']);
    assert.equal(empty.querySelector('.astra-notice').hidden, true);
  },
  trust() {
    const { el } = mount();
    assert.equal(el.querySelector('header strong').textContent, 'Brain extraction');
    assert.equal(el.querySelector('.astra-trust-message').textContent, 'Nothing here was executed.');
    assert.equal(el.querySelector('.astra-trust-message').hidden, false);
    const invalid = mount(graph({ errors: ['Invalid analysis'] })).el;
    assert.equal(invalid.querySelector('.astra-trust-message'), null);
    assert.equal(invalid.querySelector('svg'), null);
  },
  'collapsed-warnings'() {
    const warnings = ['Schema drift A', 'Schema drift B', 'Schema drift C', 'Schema drift D'];
    const { el } = mount(graph({ warnings }));
    const details = el.querySelector('.astra-warnings details');
    assert.ok(details, 'Long warning lists have a disclosure');
    assert.equal(details.open, false);
    assert.equal(details.querySelector('summary').textContent, '4 schema warnings');
    click(details.querySelector('summary'));
    assert.equal(details.open, true);
    for (const warning of warnings) assert.match(details.textContent, new RegExp(warning));
    const short = mount(graph({ warnings: warnings.slice(0, 3) })).el;
    assert.equal(short.querySelector('.astra-warnings details'), null);
    assert.match(short.querySelector('.astra-warnings').textContent, /Schema drift C/);
  },
  cleanup() {
    const model = new Model(graph());
    const seen = [];
    for (const key of ['mode', 'expanded', 'selected_node']) model.on(`change:${key}`, () => seen.push(key));
    const first = mount(model.get('graph'), model);
    const second = mount(model.get('graph'), model);
    first.cleanup();
    assert.equal(first.el.children.length, 0);
    mode(second.el, 'Evidence');
    assert.deepEqual(labels(second.el), ['Mask', 'Mask quality', 'Prior comparison']);
    second.cleanup();
    for (const [key, value] of [['mode', 'decisions'], ['expanded', ['cluster']], ['selected_node', 'stage']]) model.set(key, value);
    assert.deepEqual(seen, ['mode', 'mode', 'expanded', 'selected_node']);
    assert.equal(second.el.children.length, 0);
    for (const callbacks of model.listeners.values()) assert.equal(callbacks.size, 1, 'Only the unrelated listener survives both disposals');
    const third = mount(model.get('graph'), model);
    assert.ok(node(third.el, 'Threshold'));
    third.cleanup();
  },
};
cases[process.argv[3]]();
console.log(`PASS ${process.argv[3]}`);
