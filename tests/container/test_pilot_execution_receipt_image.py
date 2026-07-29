import sys

from testlib import resolve_source, run_cmd


def test_pilot_receipt_cli_and_schema_are_installed():
    cli = resolve_source(
        "/usr/local/bin/neurodesktop-pilot-receipt",
        "scripts/neurodesktop_pilot_receipt.py",
    )
    schema = resolve_source(
        "/opt/neurodesktop/schemas/"
        "neurodesktop-pilot-execution-receipt-v1.0.0.schema.json",
        "schemas/neurodesktop-pilot-execution-receipt-v1.0.0.schema.json",
    )

    exit_code, output = run_cmd(f"{sys.executable} {cli} --help")

    assert exit_code == 0, output
    assert "{validate,generate}" in output
    assert schema.is_file()


def test_pilot_receipt_runtime_dependencies_are_installed():
    exit_code, output = run_cmd(
        f'{sys.executable} -c "import jsonschema, rfc8785; '
        'print(rfc8785.__version__)"'
    )

    assert exit_code == 0, output
    assert output == "0.1.4"
