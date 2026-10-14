// Correctness linting for owned JavaScript. scripts/check_quality.py owns the
// file scope and passes it explicitly; this file only maps each file to the
// runtime that executes it. Paths are relative to the repository root, which
// is the working directory of the quality gate.
import globals from "globals";

const CORRECTNESS_RULES = {
  "no-undef": "error",
  "no-unused-vars": ["error", { caughtErrors: "none" }],
  "no-unreachable": "error",
  "no-unreachable-loop": "error",
  "no-fallthrough": "error",
  "no-unsafe-finally": "error",
  "no-unsafe-negation": "error",
  "no-unsafe-optional-chaining": "error",
  "no-constant-condition": "error",
  "no-constant-binary-expression": "error",
  "no-cond-assign": "error",
  "no-dupe-keys": "error",
  "no-dupe-else-if": "error",
  "no-duplicate-case": "error",
  "no-self-assign": "error",
  "no-const-assign": "error",
  "no-func-assign": "error",
  "no-import-assign": "error",
  "no-redeclare": "error",
  "getter-return": "error",
  "no-setter-return": "error",
  "no-async-promise-executor": "error",
};

const nodeScript = { sourceType: "commonjs", globals: globals.node };
// ES modules have no CommonJS wrapper variables such as require or __dirname.
const nodeModule = { sourceType: "module", globals: globals.nodeBuiltin };

export default [
  {
    linterOptions: { reportUnusedDisableDirectives: "error" },
    languageOptions: {
      ecmaVersion: "latest",
      // Files without a runtime below get only ECMAScript globals, so a new
      // file using Node or browser APIs fails until it is classified here.
      sourceType: "module",
      globals: {},
    },
    rules: CORRECTNESS_RULES,
  },
  // Node resolves the extension: .cjs is CommonJS and .mjs is ESM.
  { files: ["**/*.cjs"], languageOptions: nodeScript },
  { files: ["**/*.mjs"], languageOptions: nodeModule },
  // Plain .js under a package without "type": "module" is CommonJS in Node.
  {
    files: ["extensions/neurodesk-launcher/build_ext.js"],
    languageOptions: nodeScript,
  },
  // OpenCode imports plugins as ES modules.
  {
    files: ["config/agents/opencode_lightcone_hooks.js"],
    languageOptions: nodeModule,
  },
  // anywidget imports the viewer as an ES module in the notebook page.
  {
    files: ["extensions/astra-viewer/neurodesk_astra_view/static/index.js"],
    languageOptions: { sourceType: "module", globals: globals.browser },
  },
  // The Dockerfile appends a classic <script> tag to Guacamole's index.html.
  {
    files: ["config/guacamole/mac-clipboard-shim.js"],
    languageOptions: { sourceType: "script", globals: globals.browser },
  },
  // Served as a classic script inside the T3 iframe. web.py substitutes the
  // JSON-encoded proxy prefix for __T3_PREFIX__ before sending the file.
  {
    files: ["extensions/t3-code-server/neurodesk_t3_code/web_adapter.js"],
    languageOptions: {
      sourceType: "script",
      globals: { ...globals.browser, __T3_PREFIX__: "readonly" },
    },
  },
  // Firefox reads this with its preference parser, not a JavaScript engine.
  // The parser supplies the pref functions and the locked/sticky attributes.
  {
    files: ["config/firefox/syspref.js"],
    languageOptions: {
      sourceType: "script",
      globals: {
        pref: "readonly",
        user_pref: "readonly",
        sticky_pref: "readonly",
        locked: "readonly",
        sticky: "readonly",
      },
    },
  },
];
