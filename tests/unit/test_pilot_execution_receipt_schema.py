import copy
import json

from jsonschema import Draft202012Validator

from testlib import repo_path


SCHEMA_PATH = repo_path(
    "schemas/neurodesktop-pilot-execution-receipt-v1.0.0.schema.json"
)
FIXTURE_DIR = repo_path("tests/fixtures/pilot-execution-receipts")


def _load_json(path):
    return json.loads(path.read_text())


def _apply_mutations(document, mutations):
    mutated = copy.deepcopy(document)
    for mutation in mutations:
        target = mutated
        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in mutation["path"].lstrip("/").split("/")
        ]
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]

        leaf = parts[-1]
        if mutation["op"] == "remove":
            if isinstance(target, list):
                del target[int(leaf)]
            else:
                del target[leaf]
        elif mutation["op"] == "replace":
            if isinstance(target, list):
                target[int(leaf)] = mutation["value"]
            else:
                target[leaf] = mutation["value"]
        else:
            raise AssertionError(f"Unsupported fixture mutation: {mutation['op']}")
    return mutated


def test_complete_success_receipt_matches_the_versioned_schema():
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)

    receipt = _load_json(FIXTURE_DIR / "valid-success.json")
    Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    ).validate(receipt)


def test_timed_out_attempt_without_outputs_matches_the_versioned_schema():
    schema = _load_json(SCHEMA_PATH)
    receipt = _load_json(FIXTURE_DIR / "valid-timeout.json")

    Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    ).validate(receipt)


def test_invalid_receipts_are_rejected():
    schema = _load_json(SCHEMA_PATH)
    validator = Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )

    scenarios = _load_json(FIXTURE_DIR / "invalid-scenarios.json")
    for scenario in scenarios:
        base = _load_json(FIXTURE_DIR / scenario["baseFixture"])
        receipt = _apply_mutations(base, scenario["mutations"])
        errors = list(validator.iter_errors(receipt))
        assert errors, f"{scenario['name']} unexpectedly matched the receipt schema"
