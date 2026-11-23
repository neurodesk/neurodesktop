"""Behavioral tests for the scheduled object-storage inventory check."""

import os
import subprocess

import pytest

from testlib import repo_path


CHECKER = repo_path(".github/workflows/test_objectstorage.sh")


def _write_fake_wget(bin_dir):
    wget = bin_dir / "wget"
    wget.write_text(
        """#!/usr/bin/env bash
set -eu
output=""
for argument in "$@"; do
    case "$argument" in
        --output-document=*) output="${argument#*=}" ;;
    esac
done
if [[ "${FAKE_WGET_FAIL:-false}" == "true" ]]; then
    exit 8
fi
cp "$FAKE_INVENTORY" "$output"
"""
    )
    wget.chmod(0o755)


def _write_fake_curl(bin_dir):
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -eu
url="${!#}"
image="${url##*/}"
image="${image%.simg}"
printf '%s\n' "$image" >> "$FAKE_CURL_LOG"

if [[ "$image" == "${FAKE_TRANSPORT_IMAGE:-}" ]]; then
    printf '000'
    exit 7
fi

if [[ "$image" == "${FAKE_REDIRECT_IMAGE:-}" ]]; then
    printf '302'
    exit 0
fi

case " $FAKE_PRESENT_IMAGES " in
    *" $image "*) printf '200'; exit 0 ;;
esac

if [[ "$image" == "${FAKE_TRANSIENT_IMAGE:-}" ]]; then
    attempts=$(grep -Fxc "$image" "$FAKE_CURL_LOG")
    if [[ "$attempts" -ge "${FAKE_TRANSIENT_APPEARS_ON:-2}" ]]; then
        printf '200'
        exit 0
    fi
fi
printf '404'
exit 22
"""
    )
    curl.chmod(0o755)


def _run_checker(
    tmp_path,
    inventory,
    *,
    present=(),
    transient_image="",
    transport_image="",
    redirect_image="",
    appears_on=2,
    attempts=2,
    download_fails=False,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    inventory_file = tmp_path / "inventory.log"
    inventory_file.write_text(inventory)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_wget(bin_dir)
    _write_fake_curl(bin_dir)
    sleep_log = tmp_path / "sleep.log"
    sleep = bin_dir / "sleep"
    sleep.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$FAKE_SLEEP_LOG"\n'
    )
    sleep.chmod(0o755)
    curl_log = tmp_path / "curl.log"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_INVENTORY": str(inventory_file),
            "FAKE_WGET_FAIL": "true" if download_fails else "false",
            "FAKE_PRESENT_IMAGES": " ".join(present),
            "FAKE_TRANSIENT_IMAGE": transient_image,
            "FAKE_TRANSIENT_APPEARS_ON": str(appears_on),
            "FAKE_TRANSPORT_IMAGE": transport_image,
            "FAKE_REDIRECT_IMAGE": redirect_image,
            "FAKE_CURL_LOG": str(curl_log),
            "FAKE_SLEEP_LOG": str(sleep_log),
            "OBJECT_STORAGE_INVENTORY_URL": "https://example.invalid/log.txt",
            "OBJECT_STORAGE_CHECK_ATTEMPTS": str(attempts),
            "OBJECT_STORAGE_RETRY_DELAY": "0",
        }
    )
    result = subprocess.run(
        ["bash", str(CHECKER), "https://objects.example.invalid/neurodesk/"],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )
    requests = curl_log.read_text().splitlines() if curl_log.exists() else []
    return result, requests, sleep_log


def test_object_storage_check_accepts_a_complete_snapshot(tmp_path):
    result, requests, _ = _run_checker(
        tmp_path,
        "present-one_1.0_20260101 categories:testing,\n"
        "present-two_2.0_20260202 categories:testing,\n",
        present=("present-one_1.0_20260101", "present-two_2.0_20260202"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 2 object-storage inventory entries" in result.stdout
    assert requests == ["present-one_1.0_20260101", "present-two_2.0_20260202"]


def test_object_storage_check_retries_only_missing_images(tmp_path):
    result, requests, sleep_log = _run_checker(
        tmp_path,
        "present_1.0_20260101 categories:testing,\n"
        "late_2.0_20260202 categories:testing,\n",
        present=("present_1.0_20260101",),
        transient_image="late_2.0_20260202",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "retrying 1 missing entry" in result.stdout
    assert requests == [
        "present_1.0_20260101",
        "late_2.0_20260202",
        "late_2.0_20260202",
    ]
    assert sleep_log.read_text() == "0\n"


def test_object_storage_check_reports_every_persistent_mismatch(tmp_path):
    result, requests, _ = _run_checker(
        tmp_path,
        "present_1.0_20260101 categories:testing,\n"
        "missing-one_1.0_20260101 categories:testing,\n"
        "missing-two_2.0_20260202 categories:testing,\n",
        present=("present_1.0_20260101",),
    )

    assert result.returncode == 2
    assert "missing-one_1.0_20260101" in result.stdout
    assert "missing-two_2.0_20260202" in result.stdout
    assert "2 of 3 object-storage inventory entries are missing" in result.stdout
    assert requests.count("present_1.0_20260101") == 1
    assert requests.count("missing-one_1.0_20260101") == 2
    assert requests.count("missing-two_2.0_20260202") == 2


def test_object_storage_check_stops_on_a_transport_failure(tmp_path):
    result, requests, sleep_log = _run_checker(
        tmp_path,
        "unreachable_1.0_20260101 categories:testing,\n"
        "unchecked_2.0_20260202 categories:testing,\n",
        transport_image="unreachable_1.0_20260101",
    )

    assert result.returncode == 1
    assert "curl status 7, HTTP status 000" in result.stdout
    assert requests == ["unreachable_1.0_20260101"]
    assert not sleep_log.exists()


def test_object_storage_check_rejects_a_redirect(tmp_path):
    result, requests, sleep_log = _run_checker(
        tmp_path,
        "redirected_1.0_20260101 categories:testing,\n"
        "unchecked_2.0_20260202 categories:testing,\n",
        present=("unchecked_2.0_20260202",),
        redirect_image="redirected_1.0_20260101",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "curl status 0, HTTP status 302" in result.stdout
    assert "Validated" not in result.stdout
    assert requests == ["redirected_1.0_20260101"]
    assert not sleep_log.exists()


def test_object_storage_check_rejects_failed_or_empty_download(tmp_path):
    result, _, _ = _run_checker(tmp_path, "")
    assert result.returncode != 0
    assert "inventory download was empty" in result.stdout

    result, _, _ = _run_checker(
        tmp_path / "failed-download", "ignored\n", download_fails=True
    )
    assert result.returncode != 0
    assert "could not download object-storage inventory" in result.stdout


@pytest.mark.parametrize(
    "invalid", [" missing", "\tmissing", " invalid/name", "../outside", ".", ".."]
)
def test_object_storage_check_rejects_malformed_records(tmp_path, invalid):
    result, requests, _ = _run_checker(
        tmp_path, f"present\n{invalid}\n", present=("present",)
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "invalid container identifier" in result.stdout
    assert requests == []


def _run_catalog_checker(
    tmp_path,
    url_payloads,
    *,
    present=(),
    attempts=1,
    fail_url="",
):
    import json

    tmp_path.mkdir(parents=True, exist_ok=True)
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    route_map = {}
    for idx, (url, body) in enumerate(url_payloads.items()):
        path = payload_dir / f"payload-{idx}"
        path.write_text(body if isinstance(body, str) else json.dumps(body))
        route_map[url] = str(path)
    routes_file = tmp_path / "routes.json"
    routes_file.write_text(json.dumps(route_map))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wget_log = tmp_path / "wget.log"
    wget = bin_dir / "wget"
    wget.write_text(
        """#!/usr/bin/env python3
import json, os, shutil, sys
output = ""
user_agent = ""
url = sys.argv[-1]
for arg in sys.argv[1:]:
    if arg.startswith("--output-document="):
        output = arg.split("=", 1)[1]
    elif arg.startswith("--user-agent="):
        user_agent = arg.split("=", 1)[1]
with open(os.environ["FAKE_WGET_LOG"], "a") as fh:
    fh.write(f"{user_agent}|{url}\\n")
if url == os.environ.get("FAKE_WGET_FAIL_URL", ""):
    sys.exit(8)
routes = json.load(open(os.environ["FAKE_WGET_ROUTES"]))
if url not in routes:
    sys.exit(8)
shutil.copy(routes[url], output)
"""
    )
    wget.chmod(0o755)
    _write_fake_curl(bin_dir)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
    sleep.chmod(0o755)
    curl_log = tmp_path / "curl.log"

    env = os.environ.copy()
    env.pop("OBJECT_STORAGE_INVENTORY_URL", None)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_WGET_ROUTES": str(routes_file),
            "FAKE_WGET_LOG": str(wget_log),
            "FAKE_WGET_FAIL_URL": fail_url,
            "FAKE_PRESENT_IMAGES": " ".join(present),
            "FAKE_CURL_LOG": str(curl_log),
            "OBJECT_STORAGE_CATALOG_URL": "https://catalog.example.invalid/domains.json",
            "OBJECT_STORAGE_CHECK_ATTEMPTS": str(attempts),
            "OBJECT_STORAGE_RETRY_DELAY": "0",
        }
    )
    result = subprocess.run(
        ["bash", str(CHECKER), "https://objects.example.invalid/neurodesk/"],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )
    requests = curl_log.read_text().splitlines() if curl_log.exists() else []
    wget_calls = wget_log.read_text().splitlines() if wget_log.exists() else []
    return result, requests, wget_calls


def test_object_storage_check_reads_multi_domain_catalog_by_default(tmp_path):
    result, requests, wget_calls = _run_catalog_checker(
        tmp_path,
        {
            "https://catalog.example.invalid/domains.json": {
                "domains": {
                    "neuroimaging": {},
                    "microscopy": {},
                    "datascience": {},
                }
            },
            "https://catalog.example.invalid/neuroimaging/apps.json": {
                "elastix": {
                    "apps": {
                        "elastix 5.3.1": {"version": "20261121"},
                        "gui-elastix 5.3.1": {"version": "20261121"},
                    }
                },
                "neurodesktop-lite": {
                    "apps": {
                        "neurodesktop-lite 20260428": {"version": "20260813"},
                        "neurodesktop-lite 20260428 arm64": {"version": "20260519"},
                    }
                },
            },
            "https://catalog.example.invalid/microscopy/apps.json": {
                "elastix": {"apps": {"elastix 5.3.1": {"version": "20261121"}}},
                "nextpyp": {"apps": {"nextpyp 0.7.3": {"version": "20261118"}}},
            },
            "https://catalog.example.invalid/datascience/apps.json": {},
        },
        present=(
            "elastix_5.3.1_20261121",
            "neurodesktop-lite_20260428_20260813",
            "nextpyp_0.7.3_20261118",
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 3 object-storage inventory entries" in result.stdout
    assert requests == [
        "elastix_5.3.1_20261121",
        "neurodesktop-lite_20260428_20260813",
        "nextpyp_0.7.3_20261118",
    ]
    assert all(call.startswith("neurodesk-objectstorage-check|") for call in wget_calls)


@pytest.mark.parametrize(
    ("domains_payload", "expected_error"),
    [
        ({"domains": {}}, "has no configured domains"),
        ({"domains": {"../escape": {}}}, "invalid domain"),
        ("not-json", "invalid object-storage catalog domains.json"),
    ],
)
def test_object_storage_check_rejects_invalid_catalog_domains(
    tmp_path, domains_payload, expected_error
):
    result, requests, _ = _run_catalog_checker(
        tmp_path,
        {"https://catalog.example.invalid/domains.json": domains_payload},
    )

    assert result.returncode == 1
    assert expected_error in result.stdout
    assert requests == []


def test_object_storage_check_fails_when_domain_catalog_download_fails(tmp_path):
    result, requests, _ = _run_catalog_checker(
        tmp_path,
        {
            "https://catalog.example.invalid/domains.json": {
                "domains": {"microscopy": {}}
            }
        },
        fail_url="https://catalog.example.invalid/microscopy/apps.json",
    )

    assert result.returncode == 1
    assert "could not download object-storage inventory" in result.stdout
    assert requests == []


@pytest.mark.parametrize("bad_app", ["tool\tbad 1.0", "../escape 1.0", "foo/bar 1.0"])
def test_object_storage_check_rejects_malformed_catalog_app_identifiers(
    tmp_path, bad_app
):
    result, requests, _ = _run_catalog_checker(
        tmp_path,
        {
            "https://catalog.example.invalid/domains.json": {
                "domains": {"neuroimaging": {}}
            },
            "https://catalog.example.invalid/neuroimaging/apps.json": {
                "tool": {"apps": {bad_app: {"version": "20261118"}}}
            },
        },
        present=("tool",),
    )

    assert result.returncode == 1
    assert "invalid container identifier" in result.stdout
    assert requests == []
