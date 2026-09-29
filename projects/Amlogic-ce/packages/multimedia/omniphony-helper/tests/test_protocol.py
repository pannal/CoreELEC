#!/usr/bin/env python3
"""Focused helper protocol test using tests/fake_orender.c.

Usage: test_protocol.py <omniphony-helper> <libfake_orender.so>
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import Helper, OP_CLOSE, OP_FEED, OP_FLUSH, OP_OPEN, ST_INFO, ST_OK


def wait_for(helper, predicate, polls=500):
    for _ in range(polls):
        helper.drain()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("timed out waiting for helper output")


if len(sys.argv) != 3:
    raise SystemExit(__doc__.strip())

helper_path, library_path = map(os.path.abspath, sys.argv[1:])
h = Helper(helper_path)
h.send(OP_OPEN, f"lib={library_path}\n".encode())
wait_for(h, lambda: any(code == ST_OK and text.startswith("open ")
                         for code, text in h.status))

# The fake's first FEED is held entirely, as two packets that drain returns one
# per call; FLUSH must grow its output buffer, retry without losing the retained
# audio, call drain until it returns nothing, report metadata, emit audio, and
# only then acknowledge completion.
h.send(OP_FEED, b"held packet")
h.send(OP_FLUSH)
wait_for(h, lambda: any(code == ST_OK and text == "flush" for code, text in h.status))

flush_at = next(i for i, event in enumerate(h.events)
                if event[0] == "status" and event[2] == "flush")
audio_before = [event for event in h.events[:flush_at] if event[0] == "audio"]
assert [event[1] for event in audio_before] == [40000, 40000]
assert not any(event[0] == "audio" for event in h.events[flush_at + 1:])

lines = [text for code, text in h.status if code == ST_INFO]
assert any("source_label=DTS-HD_HRA_+_DTS:X_7.1.4" in text for text in lines)
assert any(text.endswith("bed=Lh,Rh,Ch,Lhs,Rhs") for text in lines)
assert any(" hrir=saf " in text for text in lines)

# A subsequent frame clears the declaration. The empty token is intentional:
# omission would leave the host displaying the old DTS:X label forever.
h.send(OP_FEED, b"next packet")
wait_for(h, lambda: any("source_label= bed=L,R,C,Ls,Rs" in text for code, text in h.status
                         if code == ST_INFO))

# The HRIR set is live too: the configured set landing after the first block
# is reported on its own line rather than held at the first value.
assert any(" hrir=sofa " in text for code, text in h.status if code == ST_INFO)

# Drain is idempotent and the completion acknowledgement still follows any
# output (there should be none this time).
events_before = len(h.events)
h.send(OP_FLUSH)
wait_for(h, lambda: sum(code == ST_OK and text == "flush"
                         for code, text in h.status) == 2)
second_events = h.events[events_before:]
assert not any(event[0] == "audio" for event in second_events)
assert second_events[-1][0] == "status" and second_events[-1][2] == "flush"

h.send(OP_CLOSE)
rc, stderr = h.finish()
assert rc == 0, stderr

# The decode thread's default follows the codec - on for TrueHD and E-AC-3,
# whose decoding is a large share of the work, off otherwise - and OPEN's
# decode_thread key overrides it.
for extra, want in (("codec=truehd\n", "on"), ("codec=eac3\n", "on"),
                    ("codec=dts\n", "off"), ("", "off"),
                    ("codec=eac3\ndecode_thread=off\n", "off")):
    h = Helper(helper_path)
    h.send(OP_OPEN, f"lib={library_path}\n{extra}".encode())
    wait_for(h, lambda: any(code == ST_OK and text.startswith("open ")
                             for code, text in h.status))
    opened = next(text for code, text in h.status
                  if code == ST_OK and text.startswith("open "))
    assert opened.endswith(f"decode_thread={want}"), (extra, opened)
    h.send(OP_CLOSE)
    rc, stderr = h.finish()
    assert rc == 0, stderr

print("helper protocol: PASS")
