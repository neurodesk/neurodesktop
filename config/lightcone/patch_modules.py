import hashlib
from pathlib import Path
import shutil
import sys


def patch(source: Path) -> None:
    engine = source / "src/lightcone/engine"
    expected = {
        "container.py": "7426b7cff4529b255fd060daee6757600a25ca7dec0d8140735e568610a14674",
        "identity.py": "8cb15288dc4d234cc4c5664b58f5ef14864258386348f602f06fc3d1181a9337",
        "materialize.py": "cde7a7b1f673f75c84176a09ad7e8d8f2e8148711887c2072a3da86b78fe67a9",
    }
    for name, digest in expected.items():
        if hashlib.sha256((engine / name).read_bytes()).hexdigest() != digest:
            raise ValueError(
                f"Lightcone 0.5.0 source drift in {name}; review module seams"
            )
    container = (engine / "container.py").read_text()
    anchor = '    if runtime.mode == "direct":\n        return sandbox.detect()'
    container = container.replace(
        anchor,
        "    from lightcone import neurodesk_modules\n\n"
        "    if selected := neurodesk_modules.backend(runtime):\n"
        "        return selected\n" + anchor,
    )
    container = container.replace(
        "    return sandbox.exec_policy(\n", "    ordinary = sandbox.exec_policy(\n"
    )
    anchor = "\n\ndef archive_identity("
    container = container.replace(
        anchor,
        "\n    from lightcone import neurodesk_modules\n\n"
        "    return neurodesk_modules.policy_for(runtime, ordinary)\n" + anchor,
    )
    identity = (engine / "identity.py").read_text()
    anchor = '    return f"sha256:{h.hexdigest()}"'
    identity = identity.replace(
        anchor,
        "    from lightcone import neurodesk_modules\n\n"
        "    if declared := neurodesk_modules.profile(root):\n"
        '        _frame(h, "neurodesk-modules", declared.identity())\n' + anchor,
        1,
    )
    (engine / "container.py").write_text(container)
    (engine / "identity.py").write_text(identity)
    materialize = (engine / "materialize.py").read_text()
    materialize = materialize.replace(
        "    result.sandbox = _sandbox_line(result.mode)",
        "    from lightcone import neurodesk_modules\n\n"
        "    declared = neurodesk_modules.profile(root)\n"
        "    result.sandbox = (\n"
        '        "Neurodesk modules (fs: open, network: allowed): " + ", ".join(declared.modules)\n'
        "        if declared else _sandbox_line(result.mode)\n"
        "    )",
    )
    (engine / "materialize.py").write_text(materialize)
    shutil.copyfile(
        Path(__file__).with_name("neurodesk_modules.py"),
        source / "src/lightcone/neurodesk_modules.py",
    )


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
