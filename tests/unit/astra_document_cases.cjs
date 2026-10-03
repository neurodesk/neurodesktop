const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const deps = createRequire(
  path.join(
    process.env.NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES,
    "../package.json",
  ),
);
const { JSDOM } = deps("jsdom");
const browserErrors = [];
const dom = new JSDOM(
  "<!doctype html><html><head></head><body></body></html>",
  { url: "https://notebook.test/user/alice/lab", pretendToBeVisual: true },
);
dom.window.addEventListener("error", (event) =>
  browserErrors.push(event.error),
);
for (const key of [
  "window",
  "document",
  "navigator",
  "Element",
  "HTMLElement",
  "HTMLSelectElement",
  "HTMLCanvasElement",
  "DragEvent",
  "MouseEvent",
  "KeyboardEvent",
  "Event",
  "Node",
  "DOMParser",
  "getComputedStyle",
]) {
  Object.defineProperty(globalThis, key, {
    value: dom.window[key] || dom.window.MouseEvent,
    configurable: true,
  });
}
dom.window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
});
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(
  dom.window,
);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(
  dom.window,
);
for (const key of [
  "HTMLStyleElement",
  "CSSStyleSheet",
  "Document",
  "ShadowRoot",
  "customElements",
  "MutationObserver",
])
  globalThis[key] = dom.window[key];
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "astra-document-"));
deps("esbuild").buildSync({
  stdin: {
    contents:
      "export * from '@jupyterlab/docregistry'; export {PathExt,URLExt} from '@jupyterlab/coreutils'; export {Widget} from '@lumino/widgets'; export {Signal} from '@lumino/signaling';",
    resolveDir: path.dirname(
      deps.resolve("@jupyterlab/docregistry/package.json"),
    ),
  },
  outfile: path.join(scratch, "framework.cjs"),
  bundle: true,
  platform: "browser",
  format: "cjs",
  loader: { ".css": "empty", ".svg": "text" },
  define: { "process.env.NODE_ENV": '"test"' },
});
const framework = require(path.join(scratch, "framework.cjs"));
const graph = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
let files = [],
  universes = ["b.yaml", "a.yml"],
  failDirectory = false,
  assetFailures = 0,
  held = [];
let deferGraphs = false,
  requests = [];
const contentsReads = [];
const contents = {
  fileChanged: new framework.Signal({}),
  normalize: (p) => p,
  localPath: (p) => p,
  getSharedModelFactory: () => undefined,
  driveName: () => "",
  get: async (p) => {
    contentsReads.push(p);
    if (p.endsWith("/universes"))
      return { content: universes.map((name) => ({ name, type: "file" })) };
    if (p.endsWith("astra.yaml"))
      return {
        name: "astra.yaml",
        path: p,
        type: "file",
        format: "text",
        content: "name: fixture",
        writable: false,
        last_modified: "2026-10-03T00:00:00Z",
      };
    if (failDirectory) throw new Error("directory unavailable");
    return { content: files.map((name) => ({ name, type: "file" })) };
  },
  save: async (path, options) => ({
    ...options,
    path,
    name: "astra.yaml",
    type: "file",
    format: "text",
    writable: false,
    last_modified: "2026-10-03T00:00:01Z",
  }),
  listCheckpoints: async () => [],
};
const connection = {
  makeSettings: () => ({ baseUrl: "/user/alice/" }),
  makeRequest: async (url) => {
    requests.push(url);
    if (url.endsWith("/asset/esm")) {
      if (assetFailures-- > 0) return { ok: false, status: 503 };
      return {
        ok: true,
        text: async () => fs.readFileSync(process.argv[3], "utf8"),
      };
    }
    if (url.endsWith("/asset/css")) return { ok: true, text: async () => "" };
    const query = new URL(url, "https://notebook.test").searchParams;
    const payload = structuredClone(graph);
    payload.meta.analysis_name =
      query.get("run") || query.get("universe") || "spec only";
    if (deferGraphs)
      return new Promise((resolve) => held.push({ resolve, payload }));
    return { ok: true, json: async () => payload };
  },
};
const blobs = new Map();
let blobId = 0;
const runtimeURL = class extends URL {};
runtimeURL.createObjectURL = (blob) => {
  const id = "blob:test-" + ++blobId;
  blobs.set(id, blob);
  return id;
};
runtimeURL.revokeObjectURL = (id) => blobs.delete(id);
const context = vm.createContext({
  document,
  window,
  Element,
  HTMLElement,
  Node,
  MouseEvent,
  Event,
  console,
  URL: runtimeURL,
  Blob,
  setTimeout,
  clearTimeout,
  requestAnimationFrame,
  cancelAnimationFrame,
});
const subject = new vm.SourceTextModule(
  deps("typescript").transpileModule(fs.readFileSync(process.argv[2], "utf8"), {
    compilerOptions: { target: 99, module: 99 },
  }).outputText,
  {
    context,
    importModuleDynamically: async (spec) => {
      const module = new vm.SourceTextModule(await blobs.get(spec).text(), {
        context,
      });
      await module.link(() => {
        throw Error("renderer must be offline");
      });
      await module.evaluate();
      return module;
    },
  },
);
const imports = {
  "@jupyterlab/application": { JupyterFrontEnd: {}, JupyterFrontEndPlugin: {} },
  "@jupyterlab/docregistry": framework,
  "@jupyterlab/coreutils": framework,
  "@lumino/widgets": framework,
  "@jupyterlab/services": { Contents: {}, ServerConnection: connection },
};
async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    await new Promise((r) => setTimeout(r, 5));
    if (predicate()) return;
  }
  assert.fail(document.body.textContent);
}
async function open(spec = "project/astra.yaml") {
  const registry = new framework.DocumentRegistry();
  subject.namespace.default.activate({
    docRegistry: registry,
    serviceManager: { contents },
  });
  const sessions = {
    ready: Promise.resolve(),
    refreshRunning: async () => {},
    running: () => [][Symbol.iterator](),
  };
  const manager = {
    contents,
    ready: Promise.resolve(),
    sessions,
    kernels: {},
    kernelspecs: {},
  };
  const documentContext = new framework.Context({
    manager,
    factory: new framework.TextModelFactory(),
    path: spec,
  });
  const widget = registry
    .getWidgetFactory("ASTRA Viewer")
    .createNew(documentContext);
  document.body.appendChild(widget.node);
  await documentContext.initialize(false);
  return { widget, documentContext, registry };
}
function title(widget) {
  return widget.node.querySelector(".nd-astra-host strong")?.textContent;
}
function refresh(widget) {
  widget.node.querySelector(".nd-astra-refresh").click();
}
function select(widget, value) {
  const node = widget.node.querySelector("select");
  node.value = value;
  node.dispatchEvent(new Event("change"));
}
(async () => {
  await subject.link((specifier) => {
    const exports = imports[specifier];
    assert.ok(exports, specifier);
    return new vm.SyntheticModule(
      Object.keys(exports),
      function () {
        for (const [key, value] of Object.entries(exports))
          this.setExport(key, value);
      },
      { context },
    );
  });
  await subject.evaluate();
  const test = process.argv[5];
  if (test === "initial-dispose") {
    deferGraphs = true;
    const current = await open();
    await settle(() => held.length === 1);
    current.widget.dispose();
    held[0].resolve({ ok: true, json: async () => held[0].payload });
    await new Promise((resolve) => setTimeout(resolve, 30));
    current.documentContext.model.fromString("name: saved after close");
    contentsReads.length = 0;
    await current.documentContext.save();
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(contentsReads.includes("project"), false);
    current.documentContext.dispose();
    current.registry.dispose();
  } else if (test === "registry") {
    const registry = new framework.DocumentRegistry();
    subject.namespace.default.activate({
      docRegistry: registry,
      serviceManager: { contents },
    });
    for (const name of [
      "astra.yaml",
      "astra.yml",
      "bet.astra.yaml",
      "bet.astra.yml",
    ])
      assert.equal(registry.defaultWidgetFactory(name).name, "ASTRA Viewer");
    for (const name of [
      "config.yaml",
      "myastra.yaml",
      "astra.yaml.bak",
      "astra.json",
      "universes/bet-f-0-5.yaml",
    ])
      assert.equal(
        registry
          .getFileTypesForPath(name)
          .some((type) => type.name === "astra-yaml"),
        false,
      );
    assert.equal(registry.getWidgetFactory("ASTRA Viewer").readOnly, true);
    registry.dispose();
  } else if (test === "retry") {
    assetFailures = 1;
    const first = await open();
    await settle(() => first.widget.node.textContent.includes("HTTP 503"));
    first.widget.dispose();
    first.documentContext.dispose();
    first.registry.dispose();
    const second = await open();
    await settle(() => title(second.widget) === "project/universes/a.yml");
    assert.equal(blobs.size, 0);
    second.widget.dispose();
    second.documentContext.dispose();
    second.registry.dispose();
  } else {
    const current = await open(
      test === "root-ambiguity" ? "astra.yaml" : "project/astra.yaml",
    );
    const { widget, documentContext } = current;
    await settle(() => Boolean(title(widget)));
    if (test === "discovery") {
      for (const name of [
        "run-manifest.json",
        "manifest.json",
        "status.json",
        "ro-crate-metadata.json",
      ]) {
        files = [name];
        refresh(widget);
        await settle(() => title(widget) === "project/" + name);
      }
      files = ["unrelated.json"];
      refresh(widget);
      await settle(() => title(widget) === "project/universes/a.yml");
      assert.equal(
        new URL(
          requests.filter((u) => u.includes("/graph?")).at(-1),
          "https://notebook.test",
        ).searchParams.has("run"),
        false,
      );
      failDirectory = true;
      refresh(widget);
      await settle(() => Boolean(title(widget)));
      assert.equal(
        new URL(
          requests.filter((u) => u.includes("/graph?")).at(-1),
          "https://notebook.test",
        ).searchParams.has("run"),
        false,
      );
    } else if (test === "refresh") {
      select(widget, "project/universes/b.yaml");
      await settle(() => title(widget) === "project/universes/b.yaml");
      widget.node.querySelector('[data-mode="evidence"]').click();
      files = ["status.json"];
      universes.push("c.yaml");
      refresh(widget);
      await settle(() => title(widget) === "project/status.json");
      assert.equal(
        widget.node.querySelector("select").value,
        "project/universes/b.yaml",
      );
      assert.equal(
        widget.node
          .querySelector('[data-mode="evidence"]')
          .classList.contains("active"),
        true,
      );
      files = [];
      select(widget, "");
      await settle(() => title(widget) === "spec only");
      refresh(widget);
      await settle(() => title(widget) === "spec only");
      assert.equal(widget.node.querySelector("select").value, "");
    } else if (test === "ambiguity" || test === "root-ambiguity") {
      files = ["status.json", "manifest.json"];
      refresh(widget);
      await settle(
        () => title(widget) === (test === "ambiguity" ? "project" : "."),
      );
    } else if (test === "save") {
      files = ["status.json"];
      documentContext.model.fromString("name: saved fixture");
      await documentContext.save();
      await settle(() => title(widget) === "project/status.json");
    } else if (test === "stale") {
      deferGraphs = true;
      select(widget, "project/universes/b.yaml");
      await settle(() => held.length === 1);
      select(widget, "");
      await settle(() => held.length === 2);
      held[1].resolve({ ok: true, json: async () => held[1].payload });
      await settle(() => title(widget) === "spec only");
      held[0].resolve({ ok: true, json: async () => held[0].payload });
      await new Promise((r) => setTimeout(r, 30));
      assert.equal(title(widget), "spec only");
    } else if (test === "dispose") {
      const button = widget.node.querySelector('[data-mode="evidence"]');
      deferGraphs = true;
      refresh(widget);
      await settle(() => held.length === 1);
      widget.dispose();
      held[0].resolve({ ok: true, json: async () => held[0].payload });
      await new Promise((r) => setTimeout(r, 30));
      assert.equal(widget.node.querySelector("svg"), null);
      const count = requests.length;
      documentContext.model.fromString("name: saved fixture");
      await documentContext.save();
      await new Promise((r) => setTimeout(r, 30));
      assert.equal(requests.length, count);
      button.click();
      assert.equal(button.classList.contains("active"), false);
      assert.equal(widget.node.querySelector("svg"), null);
    }
    widget.dispose();
    documentContext.dispose();
    current.registry.dispose();
  }
  assert.deepEqual(browserErrors, []);
  console.log(test + " passed");
  fs.rmSync(scratch, { recursive: true, force: true });
  dom.window.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
