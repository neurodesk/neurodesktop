"""ASTRA browser input follows the target through layout and overlay changes."""

import os
import subprocess
from urllib.parse import quote

import pytest

from test_astra_document_image import click
from test_rise_slides_image import _BidiSession, _stop, _unused_port
from test_workspace_link_routing_image import evaluate


@pytest.fixture
def browser(tmp_path):
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    port = _unused_port()
    with (tmp_path / "firefox.log").open("w") as log:
        process = subprocess.Popen(
            [
                "/usr/bin/firefox", "--headless", "--profile", str(profile),
                f"--remote-debugging-port={port}", "about:blank",
            ],
            env={**os.environ, "HOME": str(tmp_path)},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        session = None
        try:
            session = _BidiSession(f"ws://127.0.0.1:{port}/session", process)
            session.request("session.new", {"capabilities": {}})
            context = session.request("browsingContext.create", {"type": "tab"})["context"]
            html = """<!doctype html><style>
                #target {position:absolute;left:40px;top:60px;width:180px;height:40px}
                #cover {position:fixed;inset:0;z-index:10;background:white}
                </style><button id="target">analysis</button><script>
                window.clicked = [];
                document.addEventListener('click', event => clicked.push(event.target.id));
                </script>"""
            session.request("browsingContext.navigate", {
                "context": context, "url": "data:text/html," + quote(html),
                "wait": "complete",
            })
            yield session, context
        finally:
            if session:
                session.close()
            _stop(process)


def test_astra_click_follows_a_target_that_moves_before_input(browser, monkeypatch):
    session, context = browser
    request = _BidiSession.request
    moved = False

    def move_before_input(bidi, method, params):
        nonlocal moved
        if method == "input.performActions":
            result = request(bidi, "script.evaluate", {
                "expression": """(() => {
                    const target = document.querySelector('#target');
                    const before = target.getBoundingClientRect().y;
                    target.style.transform = 'translateY(120px)';
                    return target.getBoundingClientRect().y - before;
                })()""",
                "target": {"context": context}, "awaitPromise": True,
            })
            assert result["result"]["value"] == 120
            moved = True
        return request(bidi, method, params)

    monkeypatch.setattr(_BidiSession, "request", move_before_input)
    click(session, context, "document.querySelector('#target')")

    assert moved
    assert evaluate(session, context, "window.clicked.join(',')") == "target"


def test_astra_click_waits_for_a_cover_to_disappear(browser):
    session, context = browser
    evaluate(session, context, """(() => {
        const cover = document.createElement('div');
        cover.id = 'cover';
        document.body.append(cover);
        const hitTest = document.elementFromPoint.bind(document);
        window.coverWasHit = false;
        document.elementFromPoint = (x, y) => {
            const hit = hitTest(x, y);
            if (hit === cover) {
                window.coverWasHit = true;
                queueMicrotask(() => cover.remove());
            }
            return hit;
        };
        return true;
    })()""")

    click(session, context, "document.querySelector('#target')")

    assert evaluate(session, context, "window.coverWasHit")
    assert evaluate(session, context, "window.clicked.join(',')") == "target"


def test_astra_click_rejects_a_persistently_covered_target(browser):
    session, context = browser
    evaluate(session, context, """(() => {
        const cover = document.createElement('div');
        cover.id = 'cover';
        document.body.append(cover);
        return true;
    })()""")

    with pytest.raises(AssertionError, match="did not become stable and unobscured"):
        click(session, context, "document.querySelector('#target')")

    assert evaluate(session, context, "window.clicked.join(',')") == ""
