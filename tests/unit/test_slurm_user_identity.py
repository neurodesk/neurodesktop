"""Execute the upstream user endpoint before and after the image patch."""

import json
import logging
import os
import pwd
import runpy
from types import SimpleNamespace

import pytest
import tornado.web

from testlib import repo_path


PATCH = runpy.run_path(str(repo_path("config/jupyter/patch_jupyterlab_slurm.py")))
UPSTREAM = repo_path("tests/fixtures/slurm-user-handler.py").read_text()


def response(source, identity):
    namespace = {
        "APIHandler": object, "logger": logging.getLogger(__name__),
        "tornado": tornado, "os": os, "json": json,
        "make_envelope": lambda success, **fields: {"success": success, **fields},
    }
    exec(compile(source, "slurm-user-handler.py", "exec"), namespace)
    handler = namespace["UserFetchHandler"]()
    handler.current_user = identity
    handler._serverlog = logging.getLogger(__name__)
    results = []
    handler.finish = lambda value: results.append(json.loads(value))
    handler.set_status = lambda status: results.append(status)
    handler.request = SimpleNamespace(method="GET", uri="/jupyterlab_slurm/user")
    handler.get_login_url = lambda: "/login"
    handler.redirect = lambda url: results.append({"redirect": url})
    handler.get()
    return results


@pytest.mark.parametrize("identity", [
    SimpleNamespace(username="stebo85"),
    SimpleNamespace(name="hub-login"),
    "token-generated-identity",
])
@pytest.mark.parametrize("uid,account", [(1000, "jovyan"), (5000, "hpc-user")])
def test_user_filter_matches_effective_os_account(tmp_path, monkeypatch, identity, uid, account):
    monkeypatch.setenv("USER", "stale-environment-user")
    monkeypatch.setattr(os, "geteuid", lambda: uid)
    lookups = []

    def lookup(value):
        lookups.append(value)
        return SimpleNamespace(pw_name=account)

    monkeypatch.setattr(pwd, "getpwuid", lookup)
    assert response(UPSTREAM, identity)[0]["data"]["user"] != account
    path = tmp_path / "handlers.py"
    path.write_text(UPSTREAM)
    assert PATCH["patch_handler"](path)
    assert response(path.read_text(), identity) == [
        {"success": True, "data": {"user": account}}
    ]
    assert lookups == [uid]
    assert not PATCH["patch_handler"](path)


def test_patch_rejects_upstream_drift_without_writing(tmp_path):
    path = tmp_path / "handlers.py"
    changed = UPSTREAM.replace("username = None", "username = ''")
    path.write_text(changed)
    with pytest.raises(ValueError, match="anchor changed"):
        PATCH["patch_handler"](path)
    assert path.read_text() == changed


def test_user_endpoint_still_requires_authentication(tmp_path, monkeypatch):
    path = tmp_path / "handlers.py"
    path.write_text(UPSTREAM)
    PATCH["patch_handler"](path)
    monkeypatch.setattr(pwd, "getpwuid", lambda _: pytest.fail("Unauthenticated lookup"))
    result = response(path.read_text(), None)
    assert len(result) == 1
    assert result[0]["redirect"].startswith("/login?next=")


def test_missing_os_account_returns_error_not_hub_identity(tmp_path, monkeypatch):
    path = tmp_path / "handlers.py"
    path.write_text(UPSTREAM)
    PATCH["patch_handler"](path)

    def missing(_uid):
        raise KeyError("OS account missing")

    monkeypatch.setattr(pwd, "getpwuid", missing)
    result = response(path.read_text(), "hub-login")
    assert result[0] == 500
    assert result[1]["success"] is False
    assert "data" not in result[1]
