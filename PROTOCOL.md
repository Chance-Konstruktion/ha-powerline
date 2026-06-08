# Powerline Layer-2 Protocol Reference

Everything this integration knows about controlling HomePlug AV / AV2 powerline
adapters over raw Ethernet (Layer 2). The vendor-specific parts are **not in any
public spec** — they were reverse-engineered from Wireshark captures of the
official **TP-Link tpPLC** utility and verified on real hardware.

**Captured & confirmed on:**
- TP-Link **AV1000** — Broadcom **BCM60355** (uses *MEDIAXTREAM*, EtherType `0x8912`)
- Qualcomm **QCA7420** (**AV500**-class) — uses the *Qualcomm/Atheros* path, EtherType `0x88E1`

> ℹ️ "AV500 / AV600 / AV1000 / AV1200 / AV2000" are **marketing speed tiers**, not
> protocols. The two real families are **HomePlug AV** and **HomePlug AV2**
> (both IEEE 1901); they share the management layer below. What actually decides
> feature support is the **chipset** (Broadcom vs Qualcomm).

---

## 1 · EtherTypes & protocol families

| EtherType | Protocol | Used for |
|-----------|----------|----------|
| `0x88E1` | HomePlug AV (standard MMEs) | Discovery (all chipsets), Qualcomm vendor MMEs |
| `0x8912` | MEDIAXTREAM (Broadcom/Gigle) | Broadcom rates, LED, power saving, QoS, params |

Discovery + online status ride on the **standardised** `0x88E1` message
`CC_DISCOVER_LIST`, which **every** HomePlug AV/AV2 chipset answers — that is why
those two features work everywhere. Everything else is vendor-specific.

---

## 2 · Management Message Types (MMTYPE)

Verified against [`serock/mediaxtream-dissector`](https://github.com/serock/mediaxtream-dissector)
and [`serock/pla-util`](https://github.com/serock/pla-util), then confirmed live.

### Standard HomePlug AV (`0x88E1`)
| MMTYPE | Name |
|--------|------|
| `0x0014` / `0x0015` | `CC_DISCOVER_LIST` request / confirm |

### MEDIAXTREAM (`0x8912`, Broadcom)
| MMTYPE | Name | Notes |
|--------|------|-------|
| `0xA070` / `0xA071` | Discover req/cnf | not always answered |
| `0xA028` / `0xA029` | Network Info | station list |
| `0xA02C` / `0xA02D` | **Network Stats** | **PHY rates** — `pla-util get-network-stats` |
| `0xA04C` / `0xA04D` | Station Info | |
| `0xA05C` / `0xA05D` | **Get Parameter** | read a setting |
| `0xA058` / `0xA059` | **Set Parameter** | write a setting (LED, QoS, power saving …) |
| `0xA020` / `0xA021` | **Apply / commit** | sent after writes that must persist |

> ⚠️ The previous build used `0xA034` for Network Stats and `0xA080` for Station
> Info — **both wrong**; the adapter never answered, so TX/RX always read 0.

### Qualcomm / Atheros (`0x88E1` + QCA OUI `00:B0:52`)
Values from [`qca/open-plc-utils` `mme/qualcomm.h`](https://github.com/qca/open-plc-utils/blob/master/mme/qualcomm.h) (the canonical reference).

| MMTYPE | Name | Use |
|--------|------|-----|
| `0xA000` / `0xA001` | `VS_SW_VER` | firmware version — **read, safe** |
| `0xA038` / `0xA039` | `VS_NW_INFO` | network info + PHY rates — **read, safe** |
| `0xA030` / `0xA031` | `VS_LNK_STATS` | per-link statistics — **read, safe** |
| `0xA074` / `0xA075` | `VS_NW_INFO_STATS` | extended network info/stats — **read, safe** |
| `0xA024` / `0xA025` | `VS_RD_MOD` | read module (PIB/MAC) — **read, safe** |
| `0xA020` | `VS_WR_MOD` | **write** module (PIB) — ⚠️ risky |
| `0xA028` | `VS_MOD_NVM` | commit module to NVM — ⚠️ risky |
| `0xA0B0` / `0xA0B1` | module read/write (chunked) | what the **QCA7420** firmware actually uses (see §9) — ⚠️ writes the PIB |
| `0xA094` | `VS_SET_LED_BEHAVIOR` | declared in `qualcomm.h` but **never implemented** (no payload struct) |

Module codes for `VS_RD_MOD` / `VS_WR_MOD`: `VS_MODULE_MAC = 1<<0`, `VS_MODULE_PIB = 1<<1`, `VS_MODULE_FORCE = 1<<4`.

> ⚠️ Older builds used `0xA048` ("VS_NW_STATS") for QCA rates — it is **not** in
> `qualcomm.h` and never got a confirmed response. Use `VS_NW_INFO` (`0xA038`)
> or `VS_LNK_STATS` (`0xA030`) instead.

---

## 3 · MEDIAXTREAM frame format

```
 Ethernet ─┬ DST            6 bytes
           ├ SRC            6 bytes
           └ EtherType      2 bytes   = 0x8912
 MME ──────┬ Version        1 byte    = 0x02
           ├ MMTYPE         2 bytes   little-endian
           ├ FragInfo       2 bytes   = 0x0000
           ├ OUI            3 bytes   = 00 1F 84  (Gigle)
           └ SeqNum         1 byte
 Payload ── variable
```

### Get Parameter (`0xA05C`)
```
request payload : ParamID            (2 bytes LE)
confirm payload : OctetsPerElement   (1 byte)
                  NumElements        (2 bytes LE)
                  Value              (OctetsPerElement × NumElements)   ← no ParamID echo
```

### Set Parameter (`0xA058`)
```
payload : ParamID            (2 bytes LE)
          OctetsPerElement   (1 byte)
          NumElements        (2 bytes LE)
          Value              (OctetsPerElement × NumElements)
confirm : 0xA059 with empty payload = success
```

---

## 4 · Parameter IDs

| ID | Name | Notes |
|----|------|-------|
| `0x0001` | Manufacturer HFID | model string |
| `0x0024` | User NMK | network key |
| `0x0025` | User HFID | firmware / friendly name (`tpver_…`) |
| `0x0029` | **Power Manager Standby** | low 15 bits = timeout (s), **bit `0x8000` = enabled** |
| `0x003E` | LED Control | **read-only here** — always reads 0, not the real state |
| `0x003F` | **LED Options** | 4-byte; **byte 3 bit `0x10` = LED on** |
| `0x0069` | **QoS Priority Map** | ~1000-byte classifier table (see §7) |
| `0x0074` | Power-saving companion | tpPLC clears it (`=0`) when disabling power saving |
| `0x0095` | LED companion | written alongside `0x003F` (`0x0000` on / `0x0047` off) |

---

## 5 · PHY data rates (`NW_STATS` 0xA02C)

```
confirm payload : NumStations  (1 byte)
                  per station:  MAC (6)  AvgTX (2 LE)  AvgRX (2 LE)
```

**Rate encoding (confirmed on two link types):** the rate is the **low 12 bits**;
the **top nibble is a status flag**, *not* part of the value.

```
rate_mbps = raw & 0x0FFF
```

| Raw (LE) | Masked | Real (tpPLC) | Link |
|----------|--------|--------------|------|
| `0x819D` | `0x19D` = 413 | ~422 | AV1000 ↔ AV500 |
| `0x4223` | `0x223` = 547 | 547 | AV1000 ↔ AV1000 |
| `0x4221` | `0x221` = 545 | 545 | AV1000 ↔ AV1000 |

> Masking only the top bit (`0x7FFF`) was wrong — it left `0x4223` reading **16931 Mbps**.

`NW_STATS` reports the rate against the **peer** MAC, so a 2-adapter network would
only show a speed on one device. The integration mirrors the link rate onto the
responding adapter too, so both ends report a speed.

---

## 6 · LED control (Broadcom)

A three-step Set Parameter + Apply sequence (captured byte-for-byte):

| Step | MMTYPE | Param | Value ON | Value OFF |
|------|--------|-------|----------|-----------|
| 1 | `0xA058` | `0x0095` | `00 00` | `47 00` |
| 2 | `0xA058` | `0x003F` | `02 a0 01 12` | `02 a0 01 02` |
| 3 | `0xA020` | — (apply) | *(empty)* | *(empty)* |

Byte 3 of `0x003F` carries the enable bit `0x10` (`0x12` = on, `0x02` = off).
The integration **bails after step 1** if the adapter doesn't answer — a
non-MEDIAXTREAM adapter (e.g. Qualcomm) then fails fast instead of timing out.

<details><summary>Real captured frames (TL-PA7017)</summary>

```
LED OFF  A058 param=0x0095 val=4700        -> A059
         A058 param=0x003F val=02a00102     -> A059
         A020 (apply)                       -> A021
LED ON   A058 param=0x0095 val=0000         -> A059
         A058 param=0x003F val=02a00112     -> A059
         A020 (apply)                       -> A021
```
</details>

---

## 7 · Power saving (Broadcom)

Param `0x0029` is a 16-bit value encoding **both** the standby timeout (low 15
bits, seconds) **and** an enabled flag (`0x8000`) — the same flag scheme as the
PHY rate field.

| Action | Param `0x0029` | Extra |
|--------|----------------|-------|
| ON  | `timeout | 0x8000` (e.g. `0x812C` = 300 s) | Apply `0xA020` |
| OFF | `timeout` (e.g. `0x012C`) | Set `0x0074 = 00`, then Apply `0xA020` |

The integration reads the current value first and **preserves the timeout**,
toggling only the enable bit.

---

## 8 · QoS priority (Broadcom)

QoS is a **read-modify-write** of the priority-map table, param `0x0069`
(~1000 bytes). tpPLC reads it (`0xA05C`), rewrites **8 channel-access-priority
(CAP) bytes**, and writes it back (`0xA058`) — **no Apply needed**.

CAP encoding: `0x18` = CAP0 (low) · `0x38` = CAP1 · `0x58` = CAP2 · `0x78` = CAP3 (high).

**CAP bytes live at value offsets** `2, 27, 52, 77, 102, 127, 152, 177`.

| Mode | 8 CAP bytes |
|------|-------------|
| Internet | `18 18 18 18 18 18 18 18` |
| Audio / Video | `58 18 18 38 58 58 78 78` |
| Gaming | `38 18 18 38 58 58 78 78` |
| VoIP | `78 18 18 38 58 58 78 78` |

State read-back matches the live CAP bytes against these patterns.

---

## 9 · Qualcomm (QCA / AV500) — current state

### What works (read-only, safe)
Discovery (`CC_DISCOVER_LIST`), online status, firmware (`VS_SW_VER`), and the
read MMEs in §2 (`VS_NW_INFO`, `VS_LNK_STATS`, `VS_NW_INFO_STATS`). The
**Diagnose** button now sends all of these to a QCA adapter and dumps the raw
responses — that output is the starting point for decoding rates on a specific
QCA7420 firmware.

### Why control (LED / QoS / power saving) isn't implemented
There is **no safe Layer-2 control command** for these on QCA:

- `VS_SET_LED_BEHAVIOR` (`0xA094`) is declared in `qualcomm.h` but **has no
  implementation** anywhere in open-plc-utils — no payload struct, no tool uses
  it — so there is nothing to copy and no way to verify a guess.
- The only path tpPLC actually uses is a full **Parameter Information Block
  (PIB)** read-modify-write via `VS_RD_MOD` (`0xA024`) → edit → `VS_WR_MOD`
  (`0xA020`) / `VS_MOD_NVM` (`0xA028`). The PIB signature is visible in a
  capture:

  ```
  PIB-QCA7420-1.1.0.844-01-FINAL-20120919...
  QCA7420/6410/7000 MAC SW v1.1.0 Rev:01 FINAL
  Qualcomm Atheros HomePlug AV Device
  ```

  A faulty / interrupted PIB write can corrupt the config (lose the network key,
  drop the adapter off the network → factory reset). So QCA control is **not yet
  implemented**, but a capture (below) shows it is far less risky than feared.

### Decoded: LED on/off (QCA7420) — captured & diffed
A tpPLC capture (LED off → on → off) on a QCA7420 reveals the real mechanism.
The module access uses MME **`0xA0B0`** (request) / **`0xA0B1`** (confirm) — a
chunked module read/write (this firmware's variant of `VS_RD_MOD/WR_MOD`), OUI
`00:b0:52`, header `MMV(1)=00 + MMTYPE(2 LE) + OUI(3)`. tpPLC reads the whole
PIB in ~1400-byte chunks, edits it, and writes it all back.

But diffing the written PIB across the three toggles shows **only 10 bytes ever
change** — a LED-behavior table:

| State | Value | PIB offsets |
|-------|-------|-------------|
| LED **off** | `0x01` | `0x1ED5, 0x1EFD, 0x1F05, 0x1F1D, 0x1F25, 0x1F2D, 0x1F45, 0x1F4D, 0x1F55, 0x1F6D` |
| LED **on**  | `0x00` | (same offsets) |

Key safety findings:
- Writing the **same** state twice produces **byte-identical** frames (off-cycle 1
  == off-cycle 3), so there is **no per-write counter**.
- **No checksum churn**: the PIB image signature in the write "open" command
  (`…7023 0000 89c5 80ea`) is identical for on *and* off — flipping these 10 bytes
  needs no checksum recompute.

⇒ A safe 0.2 implementation replicates tpPLC exactly: **read this device's real
PIB → flip only those 10 bytes → write the chunks back → commit**. Never write a
hard-coded PIB. (Power saving and QoS will be decoded the same way from their own
captures, reusing the module read/write code.)

### How to add more QCA control safely — capture recipe
The proven method (every Broadcom feature was built this way): capture the
official **tpPLC** app performing the action against your QCA7420, then decode it.

1. Run tpPLC on a PC wired to the QCA adapter; start a Wireshark capture on that
   NIC with display filter:
   ```
   eth.type == 0x88e1
   ```
2. Toggle the LED (then QoS, then power saving) **one action at a time**, noting
   the order. Save each as a separate `.pcapng`.
3. For each action, look at the frames the **PC sends** (not the adapter's
   replies). If they are `VS_WR_MOD` (`0xA020`) PIB writes, diff the PIB bytes
   between the "on" and "off" captures to find the changed offset(s).
4. Also run the integration's **Diagnose** button and grab its QCA section — it
   shows which read MMEs your firmware answers.

Share the captures (or the changed PIB offsets) and a verified, minimal-write
QCA control path can be added here — same as `_set_led_broadcom` / `_set_qos_broadcom`.

---

## 10 · References

- [`serock/mediaxtream-dissector`](https://github.com/serock/mediaxtream-dissector) — Wireshark MEDIAXTREAM dissector
- [`serock/pla-util`](https://github.com/serock/pla-util) — Ada HomePlug AV utility
- [`jbit/powerline`](https://github.com/jbit/powerline) — Rust PLC library (Broadcom + QCA)
- [`qca/open-plc-utils`](https://github.com/qca/open-plc-utils) — Qualcomm's open toolset
- TP-Link **tpPLC** Utility — the source of all captures above
