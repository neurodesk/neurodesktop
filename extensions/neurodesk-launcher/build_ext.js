/**
 * Build helper for the neurodesk-launcher JupyterLab extension.
 *
 * This script finds the JupyterLab staging directory via Python (using
 * child_process.execFileSync so no shell quoting/substitution is involved),
 * then calls build-labextension with the correct --core-path.  This avoids
 * the fragile $(python3 -c ...) shell substitution that breaks under
 * Yarn Berry 4.x's portable shell (@yarnpkg/shell).
 */

'use strict';

const { execFileSync } = require('child_process');

// Find the JupyterLab staging directory by asking Python directly.
// We use importlib.util.find_spec to locate the package root reliably,
// falling back to python then python3 as executables.
function getStagingPath() {
  const pythonCmd = [
    'import importlib.util, pathlib',
    'spec = importlib.util.find_spec("jupyterlab")',
    'print(pathlib.Path(next(iter(spec.submodule_search_locations))) / "staging")'
  ].join('; ');
  const candidates = ['python3', 'python'];
  for (const py of candidates) {
    try {
      const result = execFileSync(py, ['-c', pythonCmd], { encoding: 'utf8' });
      const p = result.trim();
      if (p) {
        return p;
      }
    } catch (_) {
      // try next candidate
    }
  }
  return null;
}

const corePath = getStagingPath();
if (!corePath) {
  console.error('ERROR: Could not determine JupyterLab staging path. Is jupyterlab installed?');
  process.exit(1);
}

console.log('Building neurodesk-launcher with --core-path: ' + corePath);

// Use require.resolve to find build-labextension.js via Node module resolution
// rather than assuming a fixed node_modules path.
let buildLabextensionPath;
try {
  buildLabextensionPath = require.resolve('@jupyterlab/builder/lib/build-labextension');
} catch (e) {
  console.error('ERROR: Could not resolve @jupyterlab/builder/lib/build-labextension:', e.message);
  process.exit(1);
}

try {
  execFileSync(
    'node',
    [buildLabextensionPath, '--core-path', corePath, '.'],
    { stdio: 'inherit', cwd: __dirname }
  );
} catch (e) {
  process.exit(e.status || 1);
}
