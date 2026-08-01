"""Size-hygiene invariants for the built image.

Each assertion pins a deliberate build-time deletion so the saving cannot
silently regress: the duplicate vendored Claude CLI inside claude-agent-sdk,
the in-layer build-essential purge, sourcemap and bundled-test-suite
stripping, the node headers, Tomcat's default webapps, and the single-copy
NBI labextension. The build-essential purge itself is asserted in
test_additional_components.py::test_build_toolchain_removed.
"""

import glob
import os
import shutil
import subprocess
import sysconfig


def test_claude_agent_sdk_bundled_cli_removed():
    """claude-agent-sdk vendors a ~260 MB Claude Code CLI the image never
    uses: NBI resolves `claude` from PATH and passes it as cli_path, and the
    image ships its own pinned binary under /opt/jovyan_defaults."""
    import claude_agent_sdk

    package_dir = os.path.dirname(claude_agent_sdk.__file__)
    assert not os.path.exists(os.path.join(package_dir, "_bundled")), (
        "claude_agent_sdk/_bundled must be deleted at build time; it "
        "duplicates the image's own Claude CLI"
    )

    # The fallbacks the SDK and NBI actually use must still exist, otherwise
    # deleting the bundle would have broken the Claude personas.
    assert shutil.which("claude"), "`claude` must be resolvable on PATH"
    assert os.access(
        "/opt/jovyan_defaults/.local/bin/claude", os.X_OK
    ), "image-owned Claude binary missing"


def test_node_headers_removed():
    """node-gyp downloads its own headers; the apt copy is dead weight."""
    assert not os.path.exists("/usr/include/node")


def test_tomcat_default_webapps_removed():
    """Tomcat's docs/examples/manager apps are deleted, not parked."""
    assert not os.path.exists("/usr/local/tomcat/webapps.dist")
    # Guard against over-deletion: the Guacamole ROOT webapp must survive.
    assert os.path.exists("/usr/local/tomcat/webapps/ROOT/WEB-INF/web.xml")


def test_tomcat_tree_stays_world_readable():
    """Ownership/modes moved into the layers that create the tree; the
    a+rX guarantee for arbitrary-UID Apptainer sessions must survive that."""
    for path in (
        "/usr/local/tomcat/conf/server.xml",
        "/usr/local/tomcat/webapps/ROOT/index.html",
        "/usr/local/tomcat/webapps/ROOT/mac-clipboard-shim.js",
    ):
        mode = os.stat(path).st_mode
        assert mode & 0o004, f"{path} must be world-readable"


def test_bundled_python_test_suites_removed():
    """The curated heavyweight test suites are stripped from site-packages."""
    site_packages = sysconfig.get_paths()["purelib"]
    for package in ("pandas", "scipy", "numpy"):
        package_dir = os.path.join(site_packages, package)
        assert os.path.isdir(package_dir), f"{package} itself must survive"
        stray_tests = [
            path
            for path in glob.glob(os.path.join(package_dir, "**", "tests"), recursive=True)
            if os.path.isdir(path)
        ]
        assert not stray_tests, f"{package} still ships test suites: {stray_tests}"


def test_no_large_sourcemaps_remain():
    """Sourcemaps are stripped in the layers that install them. Small maps
    from future additions are tolerated; anything sizeable must be cleaned
    in its own layer rather than re-accumulating."""
    roots = [
        sysconfig.get_paths()["purelib"],
        "/opt/conda/share/jupyter",
        "/opt/code-server",
        "/usr/lib/node_modules",
    ]
    result = subprocess.run(
        ["find", *roots, "-name", "*.js.map", "-size", "+256k"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    offenders = [line for line in result.stdout.splitlines() if line]
    assert not offenders, f"large sourcemaps shipped: {offenders[:10]}"


def test_nbi_labextension_is_single_sourced():
    """The application-level NBI labextension is a symlink into the package
    copy, so patch_nbi.py's patch reaches the only real bundle and the
    ~24 MB build is not shipped twice."""
    app_dir = (
        "/opt/conda/share/jupyter/labextensions/@plmbr/notebook-intelligence"
    )
    assert os.path.islink(app_dir), f"{app_dir} must be a symlink"

    target = os.path.realpath(app_dir)
    assert os.path.isdir(target), "symlink target missing"
    assert "/site-packages/notebook_intelligence/" in target + "/", (
        f"symlink must resolve into the notebook_intelligence package, got {target}"
    )
    remote_entries = glob.glob(os.path.join(app_dir, "static", "remoteEntry*.js"))
    assert remote_entries, "remoteEntry bundle unreachable through the symlink"
