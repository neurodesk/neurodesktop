"""File activation survives an Explorer layout change before native input."""

import vscode_browser_workflow as workflow


def test_vscode_file_click_survives_explorer_relayout(tmp_path, monkeypatch):
    request = workflow._BidiSession.request
    moved = False

    def move_before_click(session, method, params):
        nonlocal moved
        if method == "input.performActions" and params["actions"][0]["id"] == "vscode-file":
            result = request(session, "script.evaluate", {
                "expression": """(() => {
                    const label = [...document.querySelectorAll('.monaco-list-row .label-name')]
                        .find(node => node.textContent === 'acceptance.txt');
                    const before = label.getBoundingClientRect().y;
                    const list = label.closest('.monaco-list');
                    list.style.transform = 'translateY(80px)';
                    return label.getBoundingClientRect().y - before;
                })()""",
                "target": {"context": params["context"]}, "awaitPromise": True,
            })
            assert result["result"]["value"] == 80
            moved = True
        return request(session, method, params)

    monkeypatch.setattr(workflow._BidiSession, "request", move_before_click)
    workflow.run_vscode_edit_workflow(tmp_path)
    assert moved, "The test did not move the Explorer row before input"
