"""Tests for the hosted-webapp redirect server fallback.

``external_webapp_redirect.py`` is installed at
``/opt/neurodesktop/external_webapp_redirect.py`` and is the command
``generate_jupyter_config.py`` registers for every ``direct_url`` webapp. It
binds a tiny redirect server that answers ``GET``/``HEAD`` on a proxied path.

The security-relevant behavior is ``validate_url``: it must reject any scheme
that is not ``http``/``https`` and any URL with no host, so a misconfigured or
maliciously crafted ``direct_url`` cannot turn the redirect server into an open
redirect to a ``javascript:`` or ``file:`` target. The handler must then emit a
``302`` redirecting to the validated target, ignoring the request path.
"""

from types import SimpleNamespace

import pytest

from testlib import load_source_module


def _load_redirect_module():
    return load_source_module(
        "external_webapp_redirect",
        "/opt/neurodesktop/external_webapp_redirect.py",
        "config/jupyter/external_webapp_redirect.py",
    )


def _make_handler(module):
    """A RedirectHandler whose low-level ``send_*`` methods are recorded.

    ``BaseHTTPRequestHandler.send_response`` logs the request line and touches
    the socket; we only care about the status code and headers the redirect
    path emits, so override those three methods on a subclass instance.
    """

    class _RecordingHandler(module.RedirectHandler):
        def __init__(self):
            self.responses = []
            self.headers = []
            self.ended = False

        def send_response(self, code):
            self.responses.append(code)

        def send_header(self, name, value):
            self.headers.append((name, value))

        def end_headers(self):
            self.ended = True

    return _RecordingHandler()


@pytest.mark.parametrize(
    "url",
    [
        "https://calmar.neurodesk.org/",
        "http://localhost:8080/path?q=1",
    ],
)
def test_validate_url_accepts_absolute_http_urls(url):
    module = _load_redirect_module()
    # Should not raise SystemExit for valid absolute http(s) URLs.
    module.validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "file:///etc/passwd",
        "data:text/html,<script>1</script>",
        "ftp://example.com/",
        "/relative/path",
        "//example.com/no-scheme",
        "https://",
        "http:///path",
        "",
    ],
)
def test_validate_url_rejects_unsafe_or_schemeless_redirects(url):
    module = _load_redirect_module()
    with pytest.raises(SystemExit, match="Invalid redirect URL"):
        module.validate_url(url)


@pytest.mark.parametrize(
    "argv",
    [
        ["external_webapp_redirect.py", "--url", "https://example.com/"],
        ["external_webapp_redirect.py", "--port", "8090"],
    ],
)
def test_parse_args_requires_url_and_port(monkeypatch, argv):
    module = _load_redirect_module()
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit):
        module.parse_args()


def test_parse_args_accepts_url_and_port(monkeypatch):
    module = _load_redirect_module()
    monkeypatch.setattr(
        "sys.argv",
        ["external_webapp_redirect.py", "--url", "https://example.com/", "--port", "8090"],
    )
    args = module.parse_args()
    assert args.url == "https://example.com/"
    assert args.port == 8090


def test_main_rejects_unsafe_url_before_starting_server(monkeypatch):
    module = _load_redirect_module()
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(url="javascript:alert(1)", port=8090),
    )

    def unexpected_server_start(*_args, **_kwargs):
        pytest.fail("server must not be constructed for an unsafe redirect URL")

    monkeypatch.setattr(module, "ThreadingHTTPServer", unexpected_server_start)

    with pytest.raises(SystemExit, match="Invalid redirect URL"):
        module.main()


def test_get_redirects_to_validated_target_url():
    module = _load_redirect_module()
    target = "https://calmar.neurodesk.org/?x=1"
    module.RedirectHandler.target_url = target

    handler = _make_handler(module)
    module.RedirectHandler.do_GET(handler)

    assert handler.responses == [302]
    assert ("Location", target) in handler.headers
    assert handler.ended is True


def test_head_also_redirects_with_302():
    module = _load_redirect_module()
    target = "https://dicompare.neurodesk.org/"
    module.RedirectHandler.target_url = target

    handler = _make_handler(module)
    module.RedirectHandler.do_HEAD(handler)

    assert handler.responses == [302]
    assert ("Location", target) in handler.headers
    assert handler.ended is True


def test_redirect_uses_whitelisted_target_not_request_path():
    """A crafted request path must not influence the redirect Location."""
    module = _load_redirect_module()
    module.RedirectHandler.target_url = "https://calmar.neurodesk.org/"

    handler = _make_handler(module)
    # Simulate a request targeting an attacker-controlled path.
    handler.path = "/../../../../evil"
    module.RedirectHandler.do_GET(handler)

    locations = [v for (n, v) in handler.headers if n == "Location"]
    assert locations == ["https://calmar.neurodesk.org/"]
