"""Exercise protocol framing used by the real desktop acceptance client."""
from testlib import GuacamoleInstructions, guacamole_instruction


def test_fragmented_and_batched_instructions_preserve_sync_and_payload():
    parser = GuacamoleInstructions()
    wire = guacamole_instruction("blob", "1", "a;4.sync,1.0;") + guacamole_instruction("sync", "123456")
    decoded = []
    for character in wire:
        decoded.extend(parser.feed(character))
    assert decoded == [["blob", "1", "a;4.sync,1.0;"], ["sync", "123456"]]
    assert guacamole_instruction(*decoded[1]) == "4.sync,6.123456;"


def test_multiple_instructions_in_one_message():
    assert GuacamoleInstructions().feed("0.,3.uid;4.sync,2.42;3.nop;") == [
        ["", "uid"], ["sync", "42"], ["nop"]
    ]
