#!/usr/bin/env python3
"""Focused helper protocol test using tests/fake_orender.c.

Usage: test_protocol.py <omniphony-helper> <libfake_orender.so> <libfake_orender_incomplete.so>

The two libraries are fake_orender.c built as it is and with
-DFAKE_INCOMPLETE, a broken install missing entry points the helper calls
(see the head of fake_orender.c).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import Helper, OP_CLOSE, OP_FEED, OP_FLUSH, OP_OPEN, ST_INFO, ST_OK, ST_OPEN_FAILED


def wait_for(helper, predicate, polls=500):
    for _ in range(polls):
        helper.drain()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("timed out waiting for helper output")


if len(sys.argv) != 4:
    raise SystemExit(__doc__.strip())

helper_path, library_path, incomplete_path = map(os.path.abspath, sys.argv[1:])
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
# The room is still loading while the first frames come out, with no
# convolution latency yet and its loudspeakers cascaded for the stand-in; the
# keys sit between hrir= and source_label=, so bed= stays last.
assert any(" hrir=saf brir=loading latency=0 render=cascade:3 source_label=" in text
           for text in lines)

# A subsequent frame clears the declaration. The empty token is intentional:
# omission would leave the host displaying the old DTS:X label forever.
h.send(OP_FEED, b"next packet")
wait_for(h, lambda: any("source_label= bed=L,R,C,Ls,Rs" in text for code, text in h.status
                         if code == ST_INFO))

# The HRIR set is live too: the configured set landing after the first block
# is reported on its own line rather than held at the first value.
assert any(" hrir=sofa " in text for code, text in h.status if code == ST_INFO)
# So is the room: once resident it is reported, with its latency, on a line
# of its own.
assert any(" brir=ready latency=127 render=room:3 " in text for code, text in h.status
           if code == ST_INFO)

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


def opened_line(h):
    wait_for(h, lambda: any(code == ST_OK and text.startswith("open ")
                             for code, text in h.status))
    return next(text for code, text in h.status
                if code == ST_OK and text.startswith("open "))


# A broken install - an engine missing an entry point the helper calls - is
# refused at OPEN by name, rather than played with part of the line missing.
h = Helper(helper_path)
h.send(OP_OPEN, f"lib={incomplete_path}\n".encode())
wait_for(h, lambda: any(code == ST_OPEN_FAILED for code, text in h.status))
assert any(code == ST_OPEN_FAILED and text == "engine is missing orender_check_config"
           for code, text in h.status), h.status
h.finish()

# The host's override: a complete config of its making, played in place of
# its config when the engine's check takes it, and only read. What the
# listener may set, and the layout and decode thread that follow, are the
# host's: OPEN's layout and decode_thread stand either way. The fake engine
# logs what it is created from.
work = tempfile.mkdtemp(prefix="omni-helper-test-")
base = os.path.join(work, "render.yaml")
layout = os.path.join(work, "cascade-12.yaml")
log = os.path.join(work, "engine.log")
overrides = {}
for name, body in (("applied", "a merged config\n"), ("rejected", "reject\n")):
    overrides[name] = os.path.join(work, f"{name}.yaml")
    with open(overrides[name], "w") as f:
        f.write(body)


def write_base():
    with open(base, "w") as f:
        f.write("base\n")


def base_text():
    with open(base) as f:
        return f.read()


def open_with(lib, extra):
    write_base()
    if os.path.exists(log):
        os.remove(log)
    env = dict(os.environ, FAKE_ORENDER_LOG=log)
    h = Helper(helper_path, env)
    h.send(OP_OPEN, f"lib={lib}\nconfig={base}\nlayout={layout}\n{extra}".encode())
    line = opened_line(h)
    h.send(OP_CLOSE)
    rc, stderr = h.finish()
    assert rc == 0, stderr
    with open(log) as f:
        calls = f.read().splitlines()
    return line, h, calls


def untouched():
    assert base_text() == "base\n"
    for name, body in (("applied", "a merged config\n"), ("rejected", "reject\n")):
        with open(overrides[name]) as f:
            assert f.read() == body, name
    assert sorted(os.listdir(work)) == ["applied.yaml", "engine.log", "rejected.yaml",
                                        "render.yaml"], os.listdir(work)


# Applied: the session is created from the override, with OPEN's layout and
# decode thread unless OPEN says what replaces them while it plays: a host
# whose listener set those in the override sends override_layout= (empty, the
# config's own) and override_decode_thread=live.
line, h, calls = open_with(library_path, f"codec=truehd\noverride={overrides['applied']}\n")
assert line.endswith(" decode_thread=on override=applied"), line
assert f"create config={overrides['applied']} layout={layout}" in calls, calls
untouched()
listener_keys = "override_layout=\noverride_decode_thread=live\n"
line, h, calls = open_with(
    library_path, f"codec=truehd\noverride={overrides['applied']}\n{listener_keys}")
assert line.endswith(" decode_thread=live override=applied"), line
assert f"create config={overrides['applied']} layout=(null)" in calls, calls
assert "set_option decode_thread=live" in calls, calls
untouched()

# Refused, the same keys change nothing: the session is what OPEN's config,
# layout and codec alone make it.
line, h, calls = open_with(
    library_path, f"codec=truehd\noverride={overrides['rejected']}\n{listener_keys}")
assert line.endswith(" decode_thread=on override=rejected"), line
assert f"create config={base} layout={layout}" in calls, calls
assert "set_option decode_thread=on" in calls, calls
untouched()

# Rejected: the host's config plays, and the reason follows the
# acknowledgement.
line, h, calls = open_with(library_path, f"codec=truehd\noverride={overrides['rejected']}\n")
assert line.endswith(" decode_thread=on override=rejected"), line
assert f"create config={base} layout={layout}" in calls, calls
opened_at = next(i for i, event in enumerate(h.events)
                 if event[0] == "status" and event[2].startswith("open "))
after = h.events[opened_at + 1]
assert after[0] == "status" and after[1] == ST_INFO, after
assert after[2] == "override_error fake: the config says reject", after
untouched()

# One that is not there is refused the same way.
line, h, calls = open_with(library_path, f"codec=dts\noverride={work}/missing.yaml\n")
assert line.endswith(" override=rejected"), line
assert f"create config={base} layout={layout}" in calls, calls
assert any(code == ST_INFO and text == "override_error fake: cannot read it"
           for code, text in h.status), h.status
untouched()

# No override asked for: the acknowledgement does not mention one.
line, h, calls = open_with(library_path, "codec=dts\n")
assert "override=" not in line, line
assert f"create config={base} layout={layout}" in calls, calls
untouched()


def run(args, stdin=b""):
    p = subprocess.run([helper_path] + args, input=stdin, capture_output=True, timeout=60)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


# --prepare-brir: one line, the file's description with what became of it,
# or failed with a reason word.
room = os.path.join(work, "room.room")
sofa = b"ROOM" * 100
described = ("hrtf=no room=yes prepared=no conventions=MultiSpeakerBRIR measurements=1 "
             "receivers=2 emitters=3 samples=12000 rate=48000 source=400-fake orientations=1 "
             "speakers=3 names=FL,FR,C")
reason = " reason=fake: 3 loudspeakers in every measurement\n"
rc, out, err = run(["--prepare-brir", library_path, room, str(len(sofa))], sofa)
assert rc == 0 and out == f"prepared {described} seconds=0.250 bytes=400{reason}", (rc, out, err)
with open(room, "rb") as f:
    assert f.read() == sofa

# The room kept for the same bytes stays, and nothing is written.
following = os.path.join(work, "room.room.next")
# The size comes last, after the kept room, as the host adds it.
rc, out, err = run(["--prepare-brir", library_path, following, room, str(len(sofa))], sofa)
assert rc == 0 and out == f"kept {described}{reason}", (rc, out, err)
assert not os.path.exists(following)
# Other bytes than the kept room's are prepared.
other = b"ROOF" * 100
rc, out, _ = run(["--prepare-brir", library_path, following, room, str(len(other))], other)
assert rc == 0 and out.startswith("prepared "), (rc, out)
with open(following, "rb") as f:
    assert f.read() == other
os.remove(following)

# What the room stage does not take: its description, nothing written.
rc, out, _ = run(["--prepare-brir", library_path, following, "8"], b"HRTFHRTF")
assert rc == 1 and out.startswith("failed reason=notroom hrtf=yes room=no "), (rc, out)
assert not os.path.exists(following)

rc, out, _ = run(["--prepare-brir", library_path, room, "6"], b"BADBAD")
assert rc == 1 and out == "failed reason=unusable fake: not a room response\n", (rc, out)

rc, out, _ = run(["--prepare-brir", library_path, room, "1000"], sofa)
assert rc == 1 and out == "failed reason=input stdin ended after 400 of 1000 bytes\n", (rc, out)

for size in ("0", "12x", "", "-5", str((4 << 30) + 1)):
    rc, out, _ = run(["--prepare-brir", library_path, room, size], sofa)
    assert rc == 1 and out.startswith("failed reason=input "), (size, rc, out)

rc, out, _ = run(["--prepare-brir", incomplete_path, room, str(len(sofa))], sofa)
assert rc == 1 and out == "failed reason=engine the engine is missing orender_brir_prepare\n", (
    rc, out)

rc, out, _ = run(["--prepare-brir", os.path.join(work, "missing.so"), room, "4"], b"ROOM")
assert rc == 1 and out.startswith("failed reason=engine "), (rc, out)

# The memory check refuses before a byte is read, when this machine is small
# enough for a file the helper accepts to exceed it.
with open("/proc/meminfo") as f:
    available = next(int(line.split()[1]) * 1024 for line in f
                     if line.startswith("MemAvailable:"))
if available <= 4 << 30:
    rc, out, _ = run(["--prepare-brir", library_path, room, str(available)], b"")
    assert rc == 1 and out.startswith("failed reason=memory needs "), (rc, out)
else:
    print(f"  (memory refusal not exercised: {available >> 20} MB available)")

# --describe: the engine's line after a word, or failed with a reason word.
rc, out, _ = run(["--describe", library_path, str(len(sofa))], sofa)
assert rc == 0 and out == ("described hrtf=no room=yes prepared=no conventions=MultiSpeakerBRIR "
                           "measurements=1 receivers=2 emitters=3 samples=12000 rate=48000 "
                           "source=400-fake orientations=1 speakers=3 names=FL,FR,C "
                           "reason=fake: 3 loudspeakers in every measurement\n"), (rc, out)
rc, out, _ = run(["--describe", library_path, "8"], b"HRTFHRTF")
assert rc == 0 and out.startswith("described hrtf=yes room=no "), (rc, out)
rc, out, _ = run(["--describe", library_path, "6"], b"BADBAD")
assert rc == 1 and out == "failed reason=unusable fake: not a SOFA file\n", (rc, out)
rc, out, _ = run(["--describe", library_path, "1000"], sofa)
assert rc == 1 and out == "failed reason=input stdin ended after 400 of 1000 bytes\n", (rc, out)
rc, out, _ = run(["--describe", incomplete_path, str(len(sofa))], sofa)
assert rc == 1 and out == "failed reason=engine the engine is missing orender_sofa_describe\n", (
    rc, out)
rc, out, err = run(["--describe", library_path], sofa)
assert rc == 2 and out == "" and err.startswith("usage: "), (rc, out, err)

# --prepare-hrtf: the staged set's grid, built or found kept, by path.
hrtf = os.path.join(work, "hrtf.sofa")
grid = os.path.join(work, "hrtf.grid")
with open(hrtf, "wb") as f:
    f.write(b"HRTF" * 100)
rc, out, _ = run(["--prepare-hrtf", library_path, hrtf, grid, "48000", "1"])
assert rc == 0 and out == "prepared grid=built seconds=0.100 bytes=16\n", (rc, out)
with open(grid) as f:
    assert f.read() == "grid 48000 1"
rc, out, _ = run(["--prepare-hrtf", library_path, hrtf, grid, "48000", "1"])
assert rc == 0 and out == "prepared grid=kept bytes=16\n", (rc, out)
rc, out, _ = run(["--prepare-hrtf", library_path, hrtf, grid, "96000", "0"])
assert rc == 0 and out.startswith("prepared grid=built "), (rc, out)
bad = os.path.join(work, "bad.sofa")
with open(bad, "wb") as f:
    f.write(b"BADBAD")
rc, out, _ = run(["--prepare-hrtf", library_path, bad, grid, "48000", "1"])
assert rc == 1 and out == "failed reason=unusable fake: not an HRTF set\n", (rc, out)
rc, out, _ = run(["--prepare-hrtf", library_path, hrtf, os.path.join(work, "no", "g"), "48000", "1"])
assert rc == 1 and out == "failed reason=write fake: cannot write\n", (rc, out)
for rate, eq in (("0", "1"), ("48k", "1"), ("", "1"), ("48000", "2"), ("48000", "")):
    rc, out, _ = run(["--prepare-hrtf", library_path, hrtf, grid, rate, eq])
    assert rc == 1 and out.startswith("failed reason=internal "), (rate, eq, rc, out)
rc, out, _ = run(["--prepare-hrtf", incomplete_path, hrtf, grid, "48000", "1"])
assert rc == 1 and out == "failed reason=engine the engine is missing orender_hrtf_prepare\n", (
    rc, out)
rc, out, err = run(["--prepare-hrtf", library_path, hrtf, grid, "48000"])
assert rc == 2 and out == "" and err.startswith("usage: "), (rc, out, err)

rc, out, err = run(["--compose", library_path, base, overrides["applied"], base])
assert rc == 2 and out == "" and err.startswith("usage: "), (rc, out, err)
assert base_text() == "base\n"

shutil.rmtree(work)
print("helper protocol: PASS")
