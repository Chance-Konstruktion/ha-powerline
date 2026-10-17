#!/usr/bin/env python3
"""Run a scenario through the real Python probe code and print one JSON line per call.

The counterpart of host_test.cpp: same scenario file, same scripted replies,
same simulated clock, but driving the integration's own ``homeplug`` package
through ``probe/powerline_probe.py``'s ``Probe``. run_compat.py diffs the two.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import struct
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# custom_components/powerline/select.py would shadow the stdlib ``select`` for
# anything imported after the path insert below; powerline_probe.py avoids that
# by importing http.server (and thus selectors) first, so do the same.
import asyncio  # noqa: E402,F401
import http.server  # noqa: E402,F401

sys.path.insert(0, str(ROOT / "custom_components" / "powerline"))
import homeplug  # noqa: E402
from homeplug import _base  # noqa: E402

_spec = importlib.util.spec_from_file_location("powerline_probe", ROOT / "probe" / "powerline_probe.py")
powerline_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(powerline_probe)


class World:
    def __init__(self):
        self.now = 1.0
        self.own = b"\x00" * 6
        self.rules = []   # (ethertype, mmtype, dst, [(delay_ms, frame)])
        self.pibs = {}    # mac bytes -> bytearray
        self.queues = {}  # ethertype -> [(avail, order, frame)]
        self.opened = set()
        self.order = 0

    def enqueue(self, et, delay_ms, frame):
        if et in self.opened:
            self.order += 1
            self.queues.setdefault(et, []).append((self.now + delay_ms / 1000.0, self.order, frame))

    def send(self, et, frame):
        mmtype = struct.unpack("<H", frame[15:17])[0]
        dst = frame[0:6]
        for r_et, r_mm, r_dst, replies, r_at in self.rules:
            if r_et != et or r_mm != mmtype:
                continue
            if r_dst == "bcast" and dst != b"\xff" * 6:
                continue
            if r_dst not in ("any", "bcast") and bytes.fromhex(r_dst) != dst:
                continue
            if r_at is not None and frame[r_at[0]:r_at[0] + len(r_at[1])] != r_at[1]:
                continue
            for delay, f in replies:
                self.enqueue(et, delay, f)
        if et == 0x88E1 and mmtype == 0xA0B0 and frame[14] == 0 and len(frame) >= 41 and dst in self.pibs:
            hdr = frame[20:41]
            clen = struct.unpack_from("<H", hdr, 17)[0]
            off = struct.unpack_from("<H", hdr, 19)[0]
            data = self.pibs[dst]
            if off + clen <= len(data):
                pl = bytearray(27)
                struct.pack_into("<I", pl, 23, off)
                reply = (self.own + dst + b"\x88\xe1" + b"\x00\xb1\xa0\x00\xb0\x52"
                         + bytes(pl) + bytes(data[off:off + clen]))
                self.enqueue(et, 0, reply)


class FakeSock:
    def __init__(self, world, et):
        self.w, self.et, self.timeout = world, et, None
        world.opened.add(et)
        world.queues[et] = []

    def settimeout(self, t):
        self.timeout = t

    def send(self, frame):
        self.w.send(self.et, frame)

    def recv(self, n):
        w = self.w
        q = w.queues.get(self.et, [])
        if q:
            q.sort(key=lambda e: (e[0], e[1]))
            avail, _, frame = q[0]
            if avail <= w.now + self.timeout + 1e-9:
                q.pop(0)
                w.now = max(w.now, avail)
                return frame
        w.now += self.timeout
        raise socket.timeout()

    def close(self):
        pass


def main(path):
    w = World()
    fake_time = types.SimpleNamespace(monotonic=lambda: w.now, sleep=lambda s: None)
    _base.time = fake_time
    hp = homeplug.HomeplugAV("fake0")

    def open_socket(self, ethertype, retries=2):
        self._src_mac = w.own
        return FakeSock(w, ethertype)

    _base._HomeplugBase._open_socket = open_socket

    # _close() must also forget queued frames, like closing a real socket
    orig_close = _base._HomeplugBase._close

    def close(self):
        orig_close(self)
        w.queues.clear()
        w.opened.clear()

    _base._HomeplugBase._close = close

    probe = powerline_probe.Probe(hp, None)
    cur = None
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        cmd = parts[0]
        if cmd == "own":
            w.own = bytes.fromhex(parts[1].replace(":", ""))
        elif cmd == "on":
            dst = parts[3].lower()
            dst = dst if dst in ("any", "bcast") else dst.replace(":", "")
            at = None
            if len(parts) > 4 and parts[4].startswith("at="):
                off, _, hx = parts[4][3:].partition(":")
                at = (int(off), bytes.fromhex(hx))
            cur = (int(parts[1], 16), int(parts[2], 16), dst, [], at)
            w.rules.append(cur)
        elif cmd == "reply":
            delay, src, et, body = int(parts[1]), parts[2], parts[3], parts[4]
            frame = w.own + bytes.fromhex(src.replace(":", "")) + bytes.fromhex(et) + bytes.fromhex(body)
            cur[3].append((delay, frame))
        elif cmd == "pib":
            w.pibs[bytes.fromhex(parts[1].replace(":", ""))] = bytearray([int(parts[3], 16)]) * int(parts[2], 16)
        elif cmd == "pibset":
            buf = w.pibs[bytes.fromhex(parts[1].replace(":", ""))]
            off = int(parts[2], 16)
            data = bytes.fromhex(parts[3])
            buf[off:off + len(data)] = data
        elif cmd == "call":
            if parts[1] == "discover":
                print(json.dumps(probe.discover(float(parts[2]))))
            elif parts[1] == "states":
                macs = [m.strip().upper() for m in (parts[2] if len(parts) > 2 else "").split(",") if m.strip()]
                print(json.dumps(probe.states(macs)))


if __name__ == "__main__":
    main(sys.argv[1])
