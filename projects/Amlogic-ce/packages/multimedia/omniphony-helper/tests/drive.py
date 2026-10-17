#!/usr/bin/env python3
"""A client of omniphony-helper's framed protocol, for its tests.

Speaks the protocol so test_real_engine.py can drive the real helper, engine
and PCM bridge, and test_protocol.py the helper against a fake engine, with
bytes they make themselves - no soundtrack required, so nothing here depends
on files that cannot be shipped.

This is a test tool, not shipped code.
"""
import os
import struct
import subprocess
import sys

HDR = 16
OP_OPEN, OP_FEED, OP_FLUSH, OP_RESET, OP_CLOSE = 1, 2, 3, 4, 5
ST = {0: "OK", 1: "PROTOCOL", 2: "OPEN_FAILED", 3: "STATE", 4: "DECODE", 5: "INFO",
      6: "BRIDGE"}
ST_OK, ST_PROTOCOL, ST_OPEN_FAILED, ST_STATE, ST_DECODE, ST_INFO, ST_BRIDGE = range(7)

# The PCM bridge's stream header, so a test can hand the renderer audio it made
# itself rather than a soundtrack nobody can ship. Layout is pcm_bridge's
# header.rs: 14 fixed bytes then one channel label per channel, in interleave
# order. Labels are bridge_api's RChannelLabel discriminants.
OPCM_MAGIC, OPCM_VERSION, OPCM_F32 = b"OPCM", 1, 1
LABEL_L, LABEL_R = 0, 1


def opcm_header(labels, sample_rate, fmt=OPCM_F32):
    return (OPCM_MAGIC
            + struct.pack("<HHIBB", OPCM_VERSION, len(labels), sample_rate, fmt, 0)
            + bytes(labels))


def opcm_tone(frames, channels, sample_rate, hz=440.0, amp=0.25, start=0):
    """`frames` of an interleaved float tone, the same in every channel.

    A tone rather than silence so that a block which arrives is provably audio
    and not a buffer nobody filled, and `start` so successive calls continue the
    waveform instead of restarting it.
    """
    import math
    vals = []
    for n in range(start, start + frames):
        v = amp * math.sin(2.0 * math.pi * hz * n / sample_rate)
        vals.extend([v] * channels)
    return struct.pack(f"<{len(vals)}f", *vals)


def cmd(op, payload=b""):
    return b"OMNC" + struct.pack("<BBHII", op, 0, 0, len(payload), 0) + payload


class Helper:
    def __init__(self, exe, env=None):
        self.p = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env)
        self.buf = b""
        self.audio = []      # (frames, pts_us)
        self.status = []     # (code, text)
        # Audio and status in the order they arrived. The helper is strictly
        # sequential - it finishes writing a packet's audio before it reads the
        # next command - so the position of the RESET status frame in this list
        # is the exact boundary between the old timeline and the new one.
        self.events = []     # ("audio", frames, pts) | ("status", code, text)
        self.err = b""       # what the helper and the engine logged, after finish()

    def send(self, op, payload=b""):
        try:
            self.p.stdin.write(cmd(op, payload))
            self.p.stdin.flush()
            return True
        except (BrokenPipeError, ValueError):
            return False

    def send_raw(self, data):
        try:
            self.p.stdin.write(data)
            self.p.stdin.flush()
            return True
        except (BrokenPipeError, ValueError):
            return False

    def drain(self, want_bytes=1 << 20):
        """Read whatever is available and parse complete frames out of it."""
        os.set_blocking(self.p.stdout.fileno(), False)
        try:
            chunk = self.p.stdout.read(want_bytes)
        except BlockingIOError:
            chunk = None
        os.set_blocking(self.p.stdout.fileno(), True)
        if chunk:
            self.buf += chunk
        self._parse()

    def read_until_quiet(self, budget=400):
        """Blocking read until the helper stops producing, bounded."""
        os.set_blocking(self.p.stdout.fileno(), True)
        for _ in range(budget):
            self.drain()
            if not self._pending():
                break
        self._parse()

    def _pending(self):
        return False

    def _parse(self):
        while len(self.buf) >= HDR:
            magic = self.buf[:4]
            if magic == b"OMNI":
                frames, pts = struct.unpack("<Iq", self.buf[4:16])
                need = HDR + frames * 2 * 4
                if len(self.buf) < need:
                    return
                self.audio.append((frames, pts))
                self.events.append(("audio", frames, pts))
                self.buf = self.buf[need:]
            elif magic == b"OMNS":
                code, ln, _ = struct.unpack("<III", self.buf[4:16])
                need = HDR + ln
                if len(self.buf) < need:
                    return
                text = self.buf[HDR:need].decode("utf-8", "replace")
                self.status.append((code, text))
                self.events.append(("status", code, text))
                self.buf = self.buf[need:]
            else:
                raise AssertionError(f"unknown magic {magic!r}")

    def finish(self, timeout=60):
        out, err = b"", b""
        try:
            out, err = self.p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            out, err = self.p.communicate()
        self.err = err or b""
        if out:
            self.buf += out
            self._parse()
        return self.p.returncode, err.decode("utf-8", "replace")


def open_payload(lib, config, bridge, codec=None, layout=None, rate=None):
    lines = [f"lib={lib}", f"config={config}", f"bridge={bridge}"]
    # Omitted rather than empty when there is none, which is what the host does
    # on its PCM path: the bridge is told the format by the stream header, and
    # an empty value would name a codec "".
    if codec:
        lines.append(f"codec={codec}")
    if layout:
        lines.append(f"layout={layout}")
    # Optional so a test can check what a host that predates the key gets, which
    # is the helper's own default rather than a failure to open.
    if rate:
        lines.append(f"rate={rate}")
    return "\n".join(lines).encode()
