"""Server-side port of JupyterLab's stream-output cursor rules.

``patch_jupyter_server_documents.py`` installs this module into the patched
package as ``jupyter_server_documents/outputs/_neurodesktop_stream.py`` and
splices thin delegating methods into ``OutputProcessor``. Keeping the logic
here, outside the patcher's string anchors, makes it reviewable as ordinary
Python and unit-testable from a checkout.

``process_stream_text`` is a line-for-line port of ``Private.processText`` in
JupyterLab's ``packages/outputarea/src/model.ts``; ``write_stream_output``
mirrors ``Private.addText``'s common-prefix diffing. The server and every
client apply these rules to the same shared Y.Text, so they must agree on the
semantics — including the surprising ones, such as ``\\n`` appending at the
end of the text rather than at the cursor. When a JupyterLab upgrade changes
``processText``, this port and the parity vectors in
``tests/unit/test_neurodesktop_stream_output.py`` must change with it.

Only ``(text_length, cursor, running_hash)`` is retained between messages,
never the text: retaining the accumulated text per cell held arbitrarily
large streams in memory for the life of the room, and comparing it on every
fragment made a fragmented stream quadratic. Fragments without ``\\r`` or
``\\b`` while the cursor sits at the end — ordinary ``print()`` output — are
blind appends that read nothing back. Rewinding fragments materialize the
text once and validate the stored cursor against an incrementally maintained
hash, so a concurrently replaced output falls back to appending at the end
instead of overwriting foreign content; JupyterLab's own client carries its
cursor between messages with no validation at all, and also materializes and
prefix-scans the full text per rewinding fragment (``Private.addText``), so
the rewind cost here matches the deployed client.

pycrdt's ``Text`` indexes by UTF-8 bytes, not code points (yrs offsets), so
every index passed to ``insert``/``del`` is converted from the Python-string
cursor arithmetic before it touches the CRDT.

One deliberate divergence: cursor arithmetic counts Unicode code points,
while JavaScript counts UTF-16 code units, so overwriting across an
astral-plane character (an emoji is two UTF-16 units but one code point)
covers a different width here than in a locally executed notebook. Exact
parity is unattainable — JavaScript's unit arithmetic can split a surrogate
pair, producing a lone-surrogate string that pycrdt/yrs cannot represent —
and code-point arithmetic is the closest well-defined behavior that never
yields invalid text. The parity tests pin this divergence explicitly.
"""

from __future__ import annotations

import hashlib

try:
    from pycrdt import Map, Text
except ImportError:  # pragma: no cover - the unit tier substitutes fakes
    Map = Text = None

CONTROL_CHARS = "\b\r\n"
REWIND_CHARS = ("\b", "\r")


def _hasher(text: str = ""):
    digest = hashlib.blake2b(digest_size=16)
    digest.update(text.encode("utf-8"))
    return digest


def process_stream_text(index: int, new_text: str, text: str = ""):
    """Apply one stream message and return ``(text, cursor)``.

    Port of JupyterLab's ``Private.processText``: overwrite printable runs at
    the cursor, move the cursor to the start of the line on ``\\r``, delete
    the previous non-newline character on ``\\b``, and append a newline at
    the end of the text on ``\\n``.
    """
    if not any(char in new_text for char in CONTROL_CHARS):
        text = text[:index] + new_text + text[index + len(new_text):]
        return text, index + len(new_text)

    cursor = index
    offset = 0
    while offset < len(new_text):
        control_positions = [
            position
            for char in CONTROL_CHARS
            if (position := new_text.find(char, offset)) >= 0
        ]
        control = min(control_positions) if control_positions else len(new_text)
        prefix = new_text[offset:control]
        text = text[:cursor] + prefix + text[cursor + len(prefix):]
        cursor += len(prefix)
        if control == len(new_text):
            break

        char = new_text[control]
        offset = control + 1
        if char == "\b":
            if cursor > 0 and text[cursor - 1] != "\n":
                text = text[:cursor - 1] + text[cursor + 1:]
                cursor -= 1
        elif char == "\r":
            cursor = text.rfind("\n", 0, cursor) + 1
        else:
            text += "\n"
            cursor = len(text)
    return text, cursor


def write_stream_output(outputs, content: dict, state=None):
    """Coalesce one stream message into *outputs* and return the new state.

    *outputs* is the cell's CRDT output array. *state* is the previous
    ``(text_length, cursor, running_hash)`` for this cell, or ``None`` when
    nothing is known — ``None`` means the cursor is at the end of whatever
    the text currently is. A contiguous same-name stream run updates the
    last output's ``Text`` in place; anything else starts a new ``Map``
    entry, so interleaved stdout/stderr stay separate outputs exactly as
    nbformat records them.
    """
    name = content["name"]
    new_text = content["text"]
    last = outputs[-1] if len(outputs) else None
    if (
        last is not None
        and last.get("output_type") == "stream"
        and last.get("name") == name
        and isinstance(last.get("text"), Text)
    ):
        ytext = last["text"]
        at_end = state is None or state[0] == state[1]
        if at_end and not any(char in new_text for char in REWIND_CHARS):
            # With the cursor at the end of the text, printable runs and
            # newlines are pure appends (the '\n' rule appends at the end of
            # the text); only '\r' and '\b' can rewind the cursor. Appending
            # needs no index and reads nothing back, so ordinary
            # newline-terminated print() output never materializes the
            # accumulated text — and it stays correct even against a
            # concurrently edited output, because '+=' appends at the real
            # end.
            ytext += new_text
            if state is None:
                return None
            state[2].update(new_text.encode("utf-8"))
            grown = len(new_text)
            return state[0] + grown, state[1] + grown, state[2]

        current = str(ytext)
        valid = (
            state is not None
            and state[0] == len(current)
            and state[2].digest() == _hasher(current).digest()
        )
        # The running hash proves the text is exactly what this writer
        # produced; anything else (another writer, a replayed room) restarts
        # conservatively at the end instead of overwriting foreign content.
        cursor = state[1] if valid else len(current)
        updated, cursor = process_stream_text(cursor, new_text, current)
        prefix = 0
        while (
            prefix < len(current)
            and prefix < len(updated)
            and current[prefix] == updated[prefix]
        ):
            prefix += 1
        # pycrdt indexes by UTF-8 bytes; convert the code-point prefix.
        byte_prefix = len(current[:prefix].encode("utf-8"))
        if prefix < len(current):
            del ytext[byte_prefix:]
        if prefix < len(updated):
            ytext.insert(byte_prefix, updated[prefix:])
        return len(updated), cursor, _hasher(updated)

    updated, cursor = process_stream_text(0, new_text)
    outputs.append(Map({
        "output_type": "stream",
        "text": Text(updated),
        "name": name,
    }))
    return len(updated), cursor, _hasher(updated)
