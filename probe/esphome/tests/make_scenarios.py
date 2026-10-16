#!/usr/bin/env python3
"""Write the scenario files in scenarios/ (replayed by host_test.cpp and ref_python.py).

Line format (see the two harnesses):

    own <mac>                          our MAC
    on <ethertype> <mmtype> <dst> [at=<offset>:<hex>]
                                       a request to answer: dst is bcast, any or a MAC;
                                       at= additionally matches request bytes
    reply <delay_ms> <src mac> <ethertype hex> <body hex>
                                       one answer to the preceding "on"
    pib <mac> <size hex> <fill hex>    synthetic PIB served for module-op reads
    pibset <mac> <offset hex> <hex>    patch bytes of it
    call discover <timeout>            run /v1/discover
    call states <mac,mac,...>          run /v1/states

Bodies reuse payloads captured on real adapters wherever the test suite
already has them (tests/test_homeplug_parsing.py, tests/test_av1300.py).
"""
from __future__ import annotations

import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent / "scenarios"
OWN = "02:00:00:00:00:99"

# MEDIAXTREAM (Broadcom) adapters, Qualcomm adapters
BA = "B0:19:21:F5:DB:A7"
BB = "EC:08:6B:54:FE:E3"
QA = "3C:52:A1:A9:1C:4F"
QB = "9C:A2:F4:B0:E7:C0"
QC = "5C:E9:31:55:0C:92"
STRANGER = "00:11:22:33:44:55"


def mac(s):
    return bytes.fromhex(s.replace(":", ""))


def hpav_body(mmtype, payload=b"", vs=False):
    """0x88E1 body after the Ethernet header: MMV + MMTYPE + FMI [+ OUI] + payload."""
    return (b"\x01" + struct.pack("<H", mmtype) + b"\x00\x00" + (b"\x00\xb0\x52" if vs else b"") + payload).hex()


def mx_body(mmtype, payload=b"", seq=1):
    return (b"\x02" + struct.pack("<H", mmtype) + b"\x00\x00" + b"\x00\x1f\x84" + bytes([seq]) + payload).hex()


def discover_cnf(*stations):
    out = bytes([len(stations)])
    for m, same in stations:
        out += mac(m) + bytes([0x01, 1 if same else 0]) + b"\x00" * 4
    return hpav_body(0x0015, out)


def mx_discover_cnf(iface, hfid):
    h = hfid.encode()
    return mx_body(0xA071, bytes([iface, len(h)]) + h)


def stats(*recs):
    out = bytes([len(recs)])
    for m, tx, rx in recs:
        out += mac(m) + struct.pack("<HH", tx, rx)
    return out


def param(octets, num, value):
    return bytes([octets]) + struct.pack("<H", num) + value


def nw_block(cco):
    return bytes.fromhex("83789fb4d88b0f") + b"\x0f\x02\x04" + mac(cco) + b"\x00"


def captured_nw_info():
    """The three real VS_NW_INFO.CNF payloads of tests/test_homeplug_parsing.py (AV1300, #108)."""
    import ast
    src = (Path(__file__).resolve().parents[3] / "tests" / "test_homeplug_parsing.py").read_text(encoding="utf-8")
    start = src.index("    FRAMES = {")
    end = src.index("    ALL = list(FRAMES)")
    frames = ast.literal_eval(src[start + len("    FRAMES = "):end].strip())
    return {QB: frames["9C:A2:F4:B0:E7:C0"], QA: frames["3C:52:A1:A9:1C:4F"], QC: frames["5C:E9:31:55:0C:92"]}


class Scenario:
    def __init__(self, name, comment):
        self.name = name
        self.lines = [f"# {comment}", f"own {OWN}"]

    def on(self, ethertype, mmtype, dst="any", at=None):
        extra = f" at={at[0]}:{at[1]}" if at else ""
        self.lines.append(f"on {ethertype:04x} {mmtype:04x} {dst}{extra}")

    def reply(self, src, ethertype, body, delay=0):
        self.lines.append(f"reply {delay} {src} {ethertype:04x} {body}")

    def raw(self, line):
        self.lines.append(line)

    def save(self):
        OUT.mkdir(exist_ok=True)
        (OUT / f"{self.name}.txt").write_text("\n".join(self.lines) + "\n")


def broadcom_two():
    s = Scenario("broadcom_two", "two Broadcom adapters, active NW_STATS path, then states via Get Parameter")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    s.reply(BB, 0x88E1, discover_cnf((BA, True), (STRANGER, False)))
    s.on(0x8912, 0xA070, "bcast")
    s.reply(BA, 0x8912, mx_discover_cnf(1, "TL-PA7017"))
    s.reply(BB, 0x8912, mx_discover_cnf(2, ""))
    # NW_STATS: own record first (capability, skipped), then the peer
    s.on(0x8912, 0xA02C, BA)
    s.reply(BA, 0x8912, mx_body(0xA02D, stats((BA, 0x683C, 0x682C), (BB, 0x4223, 0x4221))))
    s.on(0x8912, 0xA02C, BB)
    s.reply(BB, 0x8912, mx_body(0xA02D, stats((BB, 0x683C, 0x682C), (BA, 0x81A6, 0x8112))))
    # Get Parameter: manufacturer HFID (only BB lacks a model), user HFID
    s.on(0x8912, 0xA05C, BB, at=(23, "0100"))
    s.reply(BB, 0x8912, mx_body(0xA05D, param(1, 64, b"TL-PA7017 KIT\x00" * 1 + b"\x00" * 50)))
    s.on(0x8912, 0xA05C, "any", at=(23, "2500"))
    s.reply(BA, 0x8912, mx_body(0xA05D, param(1, 64, b"tpver_701E14_190426_901".ljust(64, b"\x00"))))
    # Network role (CCo)
    s.on(0x8912, 0xA028, "any", at=(23, "0001"))
    s.reply(BA, 0x8912, mx_body(0xA029, b"\x01" + nw_block(BA)))
    s.raw("call discover 3")
    # states: A has LED on, power saving on, gaming QoS; B only LED off
    caps_gaming = bytes((0x38, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78))
    table = bytearray(200)
    for off, cap in zip((2, 27, 52, 77, 102, 127, 152, 177), caps_gaming):
        table[off] = cap
    s.on(0x8912, 0xA05C, BA, at=(23, "3f00"))
    s.reply(BA, 0x8912, mx_body(0xA05D, param(1, 4, bytes.fromhex("00000112"))))
    s.on(0x8912, 0xA05C, BA, at=(23, "2900"))
    s.reply(BA, 0x8912, mx_body(0xA05D, param(2, 1, struct.pack("<H", 0x8078))))
    s.on(0x8912, 0xA05C, BA, at=(23, "6900"))
    s.reply(BA, 0x8912, mx_body(0xA05D, param(1, len(table), bytes(table))))
    s.on(0x8912, 0xA05C, BB, at=(23, "3f00"))
    s.reply(BB, 0x8912, mx_body(0xA05D, param(1, 4, bytes.fromhex("00000102"))))
    s.raw(f"call states {BA},{BB},{STRANGER.lower()},{BA.lower()}")
    s.save()


def passive_6046():
    s = Scenario("passive_6046", "Broadcom status indications arrive in the passive window")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    s.on(0x8912, 0xA070, "bcast")
    s.reply(BA, 0x8912, mx_discover_cnf(1, "HFID-A"))
    # 8 byte header (flags, tx/2, rx/2) + extra, arriving after the discover window
    s.reply(BA, 0x8912, mx_body(0x6046, bytes.fromhex("01000000") + struct.pack("<HH", 200, 150) + b"\x00" * 8), delay=2100)
    s.reply(BB, 0x8912, mx_body(0x6046, bytes.fromhex("01000000") + struct.pack("<HH", 0, 0)), delay=2200)
    s.reply(STRANGER, 0x8912, mx_body(0x6046, bytes.fromhex("0100") ), delay=2300)  # too short -> ignored
    s.on(0x8912, 0xA05C, "any", at=(23, "2500"))
    s.reply(BA, 0x8912, mx_body(0xA05D, param(1, 6, b"fw 1.2")))
    s.raw("call discover 2")
    s.save()


def qca_pure():
    s = Scenario("qca_pure", "two Qualcomm adapters (real AV1300 VS_NW_INFO frames), then PIB-based states")
    frames = captured_nw_info()
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(QA, 0x88E1, discover_cnf((QB, True)))
    s.reply(QB, 0x88E1, discover_cnf((QA, True)))
    for m, f in frames.items():
        s.on(0x88E1, 0xA038, m)
        s.reply(m, 0x88E1, f)
    # VS_SW_VER: status 0, ?, length, string
    s.on(0x88E1, 0xA000, QA)
    s.reply(QA, 0x88E1, hpav_body(0xA001, b"\x00\x00\x0bMAC-QCA7420\x00\x00", vs=True))
    s.on(0x88E1, 0xA000, QB)
    s.reply(QB, 0x88E1, hpav_body(0xA001, b"\x00\x00\x10" + b"\x00" * 16, vs=True))
    s.raw("call discover 3")
    # QA: generic AV500 PIB (9072), LEDs off, QoS audio_video, power saving on
    s.raw(f"pib {QA} 2370 00")
    for off in (0x1ED3, 0x1EFB, 0x1F03, 0x1F1B, 0x1F23, 0x1F2B, 0x1F43, 0x1F4B, 0x1F53, 0x1F6B):
        s.raw(f"pibset {QA} {off:x} 01")
    s.raw(f"pibset {QA} adc 42fa")
    s.raw(f"pibset {QA} 21ea 01")
    # QB: AV1300 PIB (22344), LEDs on, QoS internet, power saving off
    s.raw(f"pib {QB} 5748 00")
    s.raw(f"call states {QA},{QB}")
    # QC answers nothing; and an AV1300 with LEDs half-set -> led null
    s.save()


def qca_av1300_variants():
    s = Scenario("qca_av1300_variants", "AV1300 PIB with power saving on and mixed LED bytes; AVM-sized PIB")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(QA, 0x88E1, discover_cnf((QB, True), (QC, True)))
    s.on(0x88E1, 0xA038, "any")
    s.reply(QA, 0x88E1, "0139a0000000b052000052000001788862a27e690a00000f0a0000000000" + "00" * 30)
    s.reply(QB, 0x88E1, "0139a0000000b052000052000001788862a27e690a00000f0a0000000000" + "00" * 30)
    s.raw("call discover 2")
    s.raw(f"pib {QA} 5748 00")
    for off, v in {0x27D5: 0x08, 0x27D6: 0x96, 0x287E: 0x01, 0x28F8: 0x01, 0x2907: 0x02}.items():
        s.raw(f"pibset {QA} {off:x} {v:02x}")
    s.raw(f"pibset {QA} 255f 01")           # mixed LED bytes -> null
    s.raw(f"pibset {QA} adc 43fa")
    s.raw(f"pib {QB} 2644 00")              # AVM size
    s.raw(f"pibset {QB} 1ed3 01")
    s.raw(f"pibset {QB} 1ef8 05")           # not an LED byte, irrelevant
    s.raw(f"pibset {QB} 1f6b 07")           # LED table implausible -> led null
    s.raw(f"pibset {QB} adc 41fa")
    s.raw(f"call states {QA},{QB}")
    s.save()


def mixed():
    s = Scenario("mixed", "one Qualcomm and one Broadcom adapter: QCA rates, then Broadcom NW_STATS fills the rest")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(QA, 0x88E1, discover_cnf((BA, True)))
    s.reply(BA, 0x88E1, discover_cnf((QA, True)))
    s.on(0x8912, 0xA070, "bcast")
    s.reply(BA, 0x8912, mx_discover_cnf(1, "TL-PA7017"))
    s.on(0x88E1, 0xA038, QA)
    s.reply(QA, 0x88E1,
            "0139a0000000b052000052000001788862a27e690a00000f0a0000000000" + mac(QA).hex() + "12" + "00" * 44 + struct.pack("<II", 124, 140).hex())
    s.on(0x8912, 0xA02C, BA)
    s.reply(BA, 0x8912, mx_body(0xA02D, stats((BA, 0x683C, 0x682C), (QA, 0x4223, 0x4221))))
    s.on(0x8912, 0xA028, "any", at=(23, "0001"))
    s.reply(BA, 0x8912, mx_body(0xA029, b"\x01" + nw_block(QA)))
    s.raw("call discover 2")
    s.save()


def fallback_linkstats():
    s = Scenario("fallback_linkstats", "NW_STATS silent, LINK_STATS (0xA032) answers")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    s.on(0x8912, 0xA032, BA)
    s.reply(BA, 0x8912, mx_body(0xA033, stats((BA, 0x683C, 0x682C), (BB, 0x8200, 0x8210))))
    s.on(0x8912, 0xA032, BB)
    s.reply(BB, 0x8912, mx_body(0xA033, stats((BA, 0x8210, 0x8200))))
    s.raw("call discover 2")
    s.save()


def fallback_nwinfo():
    s = Scenario("fallback_nwinfo", "only NW_INFO answers: explicit 17-byte stations, then implicit layout")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    # explicit: 1 network, count byte 2, two stations of 13+4 bytes
    sta = lambda m, tx, rx: mac(m) + b"\x05" + mac(STRANGER) + struct.pack("<HH", tx, rx)
    payload = b"\x01" + nw_block(BA) + b"\x02" + sta(BB, 291, 273) + sta(QA, 100, 90)
    s.on(0x8912, 0xA028, BA, at=(23, "0001"))
    s.reply(BA, 0x8912, mx_body(0xA029, payload))
    s.raw("call discover 2")
    s.save()
    s = Scenario("fallback_nwinfo_implicit", "NW_INFO with the implicit station layout (rates 0 -> nothing found), then STATION_INFO")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    impl = b"\x01" + nw_block(BA) + mac(BB) + b"\x00" * 7 + mac(QA) + b"\x00" * 7
    s.on(0x8912, 0xA028, BA, at=(23, "0001"))
    s.reply(BA, 0x8912, mx_body(0xA029, impl))
    # STATION_INFO: junk, the MAC of BB, then plausible rates 100 / 200
    s.on(0x8912, 0xA04C, BA)
    s.reply(BA, 0x8912, mx_body(0xA04D, b"\x00\x01\x02" + mac(BB) + struct.pack("<HH", 100, 200) + b"\x00" * 6))
    s.raw("call discover 2")
    s.save()


def fallback_qca_stats():
    s = Scenario("fallback_qca_stats", "last resort: Qualcomm VS_NW_STATS (0xA048) broadcast")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    s.on(0x88E1, 0xA048, "bcast")
    s.reply(BA, 0x88E1, hpav_body(0xA049, bytes([2]) + mac(BB) + struct.pack("<HH", 150, 120)
                                  + mac(STRANGER) + struct.pack("<HH", 9, 9), vs=True))
    s.raw("call discover 2")
    s.save()


def empty():
    s = Scenario("empty", "nothing answers")
    s.raw("call discover 1")
    s.raw(f"call states {BA}")
    s.raw("call states ")
    s.save()


def odd_strings():
    s = Scenario("odd_strings", "HFID/firmware strings with NULs, whitespace, controls and non-ASCII bytes")
    s.on(0x88E1, 0x0014, "bcast")
    s.reply(BA, 0x88E1, discover_cnf((BB, True)))
    s.on(0x8912, 0xA070, "bcast")
    s.reply(BA, 0x8912, mx_discover_cnf(1, "A\x00\x00"))
    s.on(0x8912, 0xA05C, "any", at=(23, "0100"))
    s.reply(BB, 0x8912, mx_body(0xA05D, param(1, 12, b'\x00 Mod"el\\\x01\xc3\xa9 \x00')))
    s.on(0x8912, 0xA05C, "any", at=(23, "2500"))
    s.reply(BA, 0x8912, mx_body(0xA05D, param(1, 10, b"\x1f v1\x00x\t \x00\x00")))
    s.reply(BB, 0x8912, mx_body(0xA05D, param(1, 2, b"\x00 ")))
    s.raw("call discover 2")
    s.save()


if __name__ == "__main__":
    for fn in (broadcom_two, passive_6046, qca_pure, qca_av1300_variants, mixed, fallback_linkstats,
               fallback_nwinfo, fallback_qca_stats, empty, odd_strings):
        fn()
    print("written to", OUT)
