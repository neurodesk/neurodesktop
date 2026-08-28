"""Parity contract for the server-side stream-output cursor rules.

``neurodesktop_stream_output.py`` is a port of ``Private.processText`` and
``Private.addText`` from JupyterLab's ``packages/outputarea/src/model.ts``.
The vectors here encode that upstream algorithm — including its surprising
rules — so a silent behavior change in the port fails loudly. When a
JupyterLab upgrade changes ``processText``, update the port and these vectors
together.

``FakeText`` indexes by UTF-8 bytes exactly as pycrdt's ``Text`` does (yrs
offsets), so a code-point index leaking into a CRDT call fails these tests
instead of corrupting non-ASCII output in the image.
"""

import pytest

from testlib import load_source_module


def load_stream_module():
    return load_source_module(
        "neurodesktop_stream_output",
        "/opt/neurodesktop/neurodesktop_stream_output.py",
        "config/jupyter/neurodesktop_stream_output.py",
    )


class FakeText:
    """The subset of pycrdt.Text the write path uses, byte-indexed."""

    def __init__(self, text=""):
        self.value = str(text)

    def __str__(self):
        return self.value

    def __iadd__(self, text):
        self.value += text
        return self

    def __delitem__(self, key):
        assert isinstance(key, slice) and key.step is None
        data = self.value.encode("utf-8")
        start, stop, _ = key.indices(len(data))
        self.value = (data[:start] + data[stop:]).decode("utf-8")

    def insert(self, index, text):
        data = self.value.encode("utf-8")
        self.value = (
            data[:index] + text.encode("utf-8") + data[index:]
        ).decode("utf-8")


class FakeMap(dict):
    pass


def load_module_with_fakes():
    module = load_stream_module()
    module.Map = FakeMap
    module.Text = FakeText
    return module


@pytest.mark.parametrize(
    ("index", "new_text", "text", "expected"),
    [
        # Control-free runs overwrite at the cursor.
        (0, "abc", "", ("abc", 3)),
        (0, "XY", "abcd", ("XYcd", 2)),
        (1, "Z", "abcd", ("aZcd", 2)),
        (2, "", "abc", ("abc", 2)),
        # '\r' returns the cursor to the start of the current line and the
        # next printable run overwrites what is already there.
        (4, "\rXY", "abcd", ("XYcd", 2)),
        (6, "\rAA", "line1\n", ("line1\nAA", 8)),
        (8, "\rBBB", "line1\nAA", ("line1\nBBB", 9)),
        # '\r\n' keeps the overwritten line; it does not erase it.
        (3, "\r\n", "abc", ("abc\n", 4)),
        # '\b' deletes the previous character unless it is a newline or the
        # cursor is at the start of the text.
        (3, "\b\bX", "abc", ("aX", 2)),
        (0, "\bX", "abc", ("Xbc", 1)),
        (2, "\bX", "a\nbc", ("a\nXc", 3)),
        (2, "\b", "a\n", ("a\n", 2)),
        # Mid-text '\b' also drops the character at the cursor. That is what
        # JupyterLab 4.6 ships (`text.slice(0, idx0 - 1) + text.slice(idx0 +
        # 1)` in packages/outputarea/src/model.ts); parity with the deployed
        # client wins over intuition here.
        (2, "\b", "abcd", ("ad", 1)),
        # '\n' always appends at the end of the text and moves the cursor
        # there, even when the cursor sits mid-text. This mirrors JupyterLab.
        (1, "\n", "abc", ("abc\n", 4)),
        # Deliberate divergence, pinned: this port counts code points, so an
        # astral-plane overwrite covers one character where JavaScript's
        # UTF-16 arithmetic covers two ("\U0001f642CDE", cursor 2 in
        # JupyterLab). JS unit arithmetic can also split a surrogate pair
        # into a string pycrdt/yrs cannot store, so exact parity is
        # unattainable; see the module docstring.
        (5, "\r\U0001f642", "ABCDE", ("\U0001f642BCDE", 1)),
    ],
)
def test_process_stream_text_matches_jupyterlab_rules(
    index, new_text, text, expected
):
    module = load_stream_module()
    assert module.process_stream_text(index, new_text, text) == expected


def test_carriage_return_fragments_coalesce_into_one_output():
    module = load_module_with_fakes()
    outputs = []

    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "stream-run-1\n"}
    )
    for fragment in range(20):
        state = module.write_stream_output(
            outputs,
            {"name": "stdout", "text": f"\rstream-fragment-{fragment:02d}"},
            state,
        )
    state = module.write_stream_output(outputs, {"name": "stdout", "text": "\n"}, state)
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "stream-end\n"}, state
    )

    assert len(outputs) == 1
    assert isinstance(outputs[0], FakeMap)
    assert isinstance(outputs[0]["text"], FakeText)
    assert outputs[0]["name"] == "stdout"
    assert str(outputs[0]["text"]) == "stream-run-1\nstream-fragment-19\nstream-end\n"


def test_backspace_fragments_are_applied_to_the_shared_text():
    module = load_module_with_fakes()
    outputs = []

    state = module.write_stream_output(outputs, {"name": "stdout", "text": "abc"})
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "\b\bXY\n"}, state
    )

    assert len(outputs) == 1
    assert str(outputs[0]["text"]) == "aXY\n"


def test_interleaved_stream_names_stay_separate_outputs():
    module = load_module_with_fakes()
    outputs = []

    state = module.write_stream_output(outputs, {"name": "stdout", "text": "out-1\n"})
    module.write_stream_output(outputs, {"name": "stderr", "text": "err-1\n"})
    module.write_stream_output(outputs, {"name": "stdout", "text": "out-2\n"})

    assert [output["name"] for output in outputs] == ["stdout", "stderr", "stdout"]
    assert [str(output["text"]) for output in outputs] == [
        "out-1\n",
        "err-1\n",
        "out-2\n",
    ]
    # The stale stdout state must not resurrect the first output's cursor.
    assert state[:2] == (6, 6)


def test_end_of_stream_appends_avoid_materializing_the_text():
    """Newline-terminated print() output must never rebuild the full text."""
    module = load_module_with_fakes()
    outputs = []

    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "line-1\n"}
    )
    text = outputs[0]["text"]

    class ExplodingStr(FakeText):
        def __str__(self):
            raise AssertionError("append fast path materialized the text")

    text.__class__ = ExplodingStr
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "line-2\nline-3\n"}, state
    )
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "partial"}, state
    )
    assert state[:2] == (28, 28)
    assert text.value == "line-1\nline-2\nline-3\npartial"

    # A rewinding control character leaves the fast path and applies the
    # full cursor rules again; the running hash still validates because the
    # fast path maintained it incrementally.
    text.__class__ = FakeText
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "\rX\n"}, state
    )
    assert text.value == "line-1\nline-2\nline-3\nXartial\n"
    assert state[:2] == (29, 29)


def test_unknown_state_still_appends_without_reading_the_text():
    """A replayed room starts with no state; appends must stay blind."""
    module = load_module_with_fakes()
    outputs = [
        FakeMap(
            {
                "output_type": "stream",
                "name": "stdout",
                "text": FakeText("replayed\n"),
            }
        )
    ]

    class ExplodingStr(FakeText):
        def __str__(self):
            raise AssertionError("blind append materialized the text")

    outputs[0]["text"].__class__ = ExplodingStr
    state = module.write_stream_output(outputs, {"name": "stdout", "text": "more\n"})
    assert state is None
    assert outputs[0]["text"].value == "replayed\nmore\n"


def test_stale_state_falls_back_to_the_end_of_the_text():
    module = load_module_with_fakes()
    outputs = [
        FakeMap(
            {"output_type": "stream", "name": "stdout", "text": FakeText("abcd")}
        )
    ]

    # A recorded length that no longer matches means another writer touched
    # the text; the mid-cursor overwrite restarts at the end instead of
    # guessing a position inside foreign content.
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "X"}, (3, 1, module._hasher("abc"))
    )
    assert str(outputs[0]["text"]) == "abcdX"
    assert state[:2] == (5, 5)


def test_equal_length_replacement_is_detected_by_the_running_hash():
    """Same length, different content must not reuse the stored cursor."""
    module = load_module_with_fakes()
    outputs = [
        FakeMap(
            {"output_type": "stream", "name": "stdout", "text": FakeText("wxyz")}
        )
    ]

    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "Q"}, (4, 1, module._hasher("abcd"))
    )
    assert str(outputs[0]["text"]) == "wxyzQ"
    assert state[:2] == (5, 5)


def test_crdt_indices_are_utf8_byte_offsets():
    """A code-point index into pycrdt's byte-indexed Text corrupts non-ASCII
    output; the byte-accurate fake raises on a mid-character edit."""
    module = load_module_with_fakes()
    outputs = []

    state = module.write_stream_output(outputs, {"name": "stdout", "text": "a\U0001f642bc"})
    state = module.write_stream_output(
        outputs, {"name": "stdout", "text": "\ra\U0001f642XY"}, state
    )
    assert str(outputs[0]["text"]) == "a\U0001f642XY"
    assert state[:2] == (4, 4)


def test_foreign_plain_text_output_starts_a_new_crdt_entry():
    module = load_module_with_fakes()
    outputs = [
        FakeMap({"output_type": "stream", "name": "stdout", "text": "plain"})
    ]

    module.write_stream_output(outputs, {"name": "stdout", "text": "new\n"})

    assert len(outputs) == 2
    assert isinstance(outputs[1]["text"], FakeText)
    assert str(outputs[1]["text"]) == "new\n"
