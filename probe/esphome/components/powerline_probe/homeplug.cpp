#include "homeplug.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace esphome {
namespace powerline_probe {
namespace hp {

// ===========================================================================
//  small helpers
// ===========================================================================

static inline uint16_t le16(const uint8_t *p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
static inline uint32_t le32(const uint8_t *p) {
  return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) |
         (static_cast<uint32_t>(p[3]) << 24);
}

static Mac mac_at(const uint8_t *p) {
  Mac m;
  std::memcpy(m.data(), p, 6);
  return m;
}

std::string mac_to_str(const uint8_t *b) {
  char buf[18];
  std::snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X", b[0], b[1], b[2], b[3], b[4], b[5]);
  return std::string(buf);
}

bool mac_from_str(const std::string &s, Mac &out) {
  uint8_t digits[12];
  size_t n = 0;
  for (char c : s) {
    if (c == ':' || c == '-' || c == ' ')
      continue;
    int v;
    if (c >= '0' && c <= '9')
      v = c - '0';
    else if (c >= 'a' && c <= 'f')
      v = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F')
      v = c - 'A' + 10;
    else
      return false;
    if (n >= 12)
      return false;
    digits[n++] = static_cast<uint8_t>(v);
  }
  if (n != 12)
    return false;
  for (size_t i = 0; i < 6; i++)
    out[i] = static_cast<uint8_t>((digits[2 * i] << 4) | digits[2 * i + 1]);
  return true;
}

// bytes.decode("ascii", errors="ignore")
static std::string ascii_ignore(const uint8_t *p, size_t n) {
  std::string s;
  s.reserve(n);
  for (size_t i = 0; i < n; i++)
    if (p[i] < 0x80)
      s.push_back(static_cast<char>(p[i]));
  return s;
}

static std::string rstrip_nul(std::string s) {
  while (!s.empty() && s.back() == '\0')
    s.pop_back();
  return s;
}

static bool py_space(char c) {
  return c == ' ' || (c >= '\t' && c <= '\r') || (c >= 0x1c && c <= 0x1f);
}

// s.strip("\x00").strip()
static std::string strip_nul_then_ws(const std::string &in) {
  size_t b = 0, e = in.size();
  while (b < e && in[b] == '\0')
    b++;
  while (e > b && in[e - 1] == '\0')
    e--;
  while (b < e && py_space(in[b]))
    b++;
  while (e > b && py_space(in[e - 1]))
    e--;
  // then keep printable ASCII only, like _hfid_text(): some Broadcom
  // adapters answer the manufacturer HFID with a lone 0x02
  std::string out;
  for (size_t k = b; k < e; k++)
    if (in[k] >= ' ' && in[k] <= '~')
      out += in[k];
  size_t ob = 0, oe = out.size();
  while (ob < oe && out[ob] == ' ')
    ob++;
  while (oe > ob && out[oe - 1] == ' ')
    oe--;
  return out.substr(ob, oe - ob);
}

const char *chip_name(Chip c) {
  switch (c) {
    case Chip::BROADCOM:
      return "broadcom";
    case Chip::QUALCOMM:
      return "qualcomm";
    default:
      return "unknown";
  }
}

// ===========================================================================
//  frame builders (frames.py)
// ===========================================================================

static void pad_to_min(Bytes &f) {
  if (f.size() < ETH_MIN)
    f.resize(ETH_MIN, 0);
}

static void put_eth(Bytes &f, const Mac &dst, const Mac &src, uint16_t ethertype) {
  f.insert(f.end(), dst.begin(), dst.end());
  f.insert(f.end(), src.begin(), src.end());
  f.push_back(static_cast<uint8_t>(ethertype >> 8));
  f.push_back(static_cast<uint8_t>(ethertype & 0xFF));
}

Bytes build_hpav_frame(const Mac &dst, const Mac &src, uint16_t mmtype, const uint8_t *payload, size_t n) {
  Bytes f;
  f.reserve(std::max<size_t>(ETH_MIN, ETH_HDR + 5 + n));
  put_eth(f, dst, src, ETHERTYPE_HPAV);
  f.push_back(0x01);  // MMV
  f.push_back(static_cast<uint8_t>(mmtype & 0xFF));
  f.push_back(static_cast<uint8_t>(mmtype >> 8));
  f.push_back(0x00);  // FMI
  f.push_back(0x00);
  if (payload != nullptr)
    f.insert(f.end(), payload, payload + n);
  pad_to_min(f);
  return f;
}

Bytes build_qca_frame(const Mac &dst, const Mac &src, uint16_t mmtype, const uint8_t *payload, size_t n) {
  Bytes p = {0x00, 0xB0, 0x52};  // QCA OUI
  if (payload != nullptr)
    p.insert(p.end(), payload, payload + n);
  return build_hpav_frame(dst, src, mmtype, p.data(), p.size());
}

Bytes build_qca_vs0_frame(const Mac &dst, const Mac &src, uint16_t mmtype, const uint8_t *payload, size_t n) {
  Bytes f;
  f.reserve(std::max<size_t>(ETH_MIN, ETH_HDR + 6 + n));
  put_eth(f, dst, src, ETHERTYPE_HPAV);
  f.push_back(0x00);  // MMV 0, no FMI on the wire
  f.push_back(static_cast<uint8_t>(mmtype & 0xFF));
  f.push_back(static_cast<uint8_t>(mmtype >> 8));
  f.push_back(0x00);
  f.push_back(0xB0);
  f.push_back(0x52);
  if (payload != nullptr)
    f.insert(f.end(), payload, payload + n);
  pad_to_min(f);
  return f;
}

Bytes build_mx_frame(const Mac &dst, const Mac &src, uint16_t mmtype, uint8_t seq, const uint8_t *payload, size_t n) {
  Bytes f;
  f.reserve(std::max<size_t>(ETH_MIN, ETH_HDR + 9 + n));
  put_eth(f, dst, src, ETHERTYPE_MEDIAXTREAM);
  f.push_back(0x02);  // version
  f.push_back(static_cast<uint8_t>(mmtype & 0xFF));
  f.push_back(static_cast<uint8_t>(mmtype >> 8));
  f.push_back(0x00);  // FragInfo
  f.push_back(0x00);
  f.push_back(0x00);  // Gigle OUI 00:1f:84
  f.push_back(0x1F);
  f.push_back(0x84);
  f.push_back(seq);
  if (payload != nullptr)
    f.insert(f.end(), payload, payload + n);
  pad_to_min(f);
  return f;
}

// ===========================================================================
//  parsers (parsers.py)
// ===========================================================================

int decode_phy_rate(uint16_t raw) {
  // Bit 11 marks a rated PLC capability, not a measured link rate -> 0.
  if (raw & 0x0800)
    return 0;
  return raw & 0x0FFF;
}

std::vector<Station> parse_discover_cnf(const uint8_t *d, size_t n) {
  std::vector<Station> out;
  size_t off = ETH_HDR + HPAV_MME_HDR;
  if (n < off + 1)
    return out;
  unsigned cnt = d[off];
  off += 1;
  for (unsigned i = 0; i < cnt; i++) {
    if (off + 12 > n)
      break;
    Station s;
    s.mac = mac_at(d + off);
    s.same_network = d[off + 7] == 1;
    off += 12;
    out.push_back(s);
  }
  return out;
}

bool parse_mx_discover_cnf(const uint8_t *d, size_t n, MxDiscover &out) {
  size_t off = ETH_HDR + MX_MME_HDR;
  size_t pn = n > off ? n - off : 0;
  const uint8_t *p = d + off;
  if (pn < 2)
    return false;
  out.ethernet = p[0] == 0x01;
  size_t hfid_len = p[1];
  out.hfid.clear();
  if (hfid_len > 0 && pn >= 2 + hfid_len)
    out.hfid = rstrip_nul(ascii_ignore(p + 2, hfid_len));
  return true;
}

NwInfo parse_mx_nw_info_cnf(const uint8_t *d, size_t n) {
  NwInfo result;
  size_t off = ETH_HDR + MX_MME_HDR;
  size_t pn = n > off ? n - off : 0;
  const uint8_t *p = d + off;
  if (pn < 1)
    return result;

  unsigned num_nw = p[0];
  size_t q = 1;
  for (unsigned i = 0; i < num_nw; i++) {
    if (q + 17 > pn)
      break;
    Network nw;
    nw.cco_mac = mac_at(p + q + 10);
    q += 17;
    result.networks.push_back(nw);
  }

  if (q >= pn)
    return result;

  size_t remaining = pn - q;
  bool parse_implicit = false;
  if (remaining > 0) {
    unsigned num_sta = p[q];
    size_t expected_min = q + 1 + static_cast<size_t>(num_sta) * 13;
    static const uint8_t zeros[6] = {0, 0, 0, 0, 0, 0};
    if (num_sta == 0 && remaining >= 6 && std::memcmp(p + q, zeros, 6) != 0) {
      parse_implicit = true;
    } else if (num_sta > 0 && expected_min <= pn) {
      q += 1;
      for (unsigned i = 0; i < num_sta; i++) {
        if (q + 13 > pn)
          break;
        Station s;
        s.mac = mac_at(p + q);
        int tx = 0, rx = 0;
        if (q + 17 <= pn) {
          tx = le16(p + q + 13);
          rx = le16(p + q + 15);
          q += 17;
        } else if (q + 15 <= pn) {
          tx = p[q + 13] * 2;
          rx = p[q + 14] * 2;
          q += 15;
        } else {
          q += 13;
        }
        s.tx_rate = tx;
        s.rx_rate = rx;
        result.stations.push_back(s);
      }
      return result;
    } else {
      parse_implicit = true;
    }
  }

  if (parse_implicit) {
    while (q + 6 <= pn) {
      bool zero = true;
      for (int i = 0; i < 6; i++)
        if (p[q + i] != 0)
          zero = false;
      if (zero)
        break;
      Station s;
      s.mac = mac_at(p + q);
      result.stations.push_back(s);
      size_t step = (q + 13 <= pn) ? 13 : 6;
      q += step;
    }
  }
  return result;
}

Bytes parse_mx_get_param_cnf(const uint8_t *d, size_t n) {
  size_t off = ETH_HDR + MX_MME_HDR;
  size_t pn = n > off ? n - off : 0;
  const uint8_t *p = d + off;
  if (pn < 3)
    return Bytes();
  unsigned octets = p[0];
  unsigned num = le16(p + 1);
  if ((octets == 1 || octets == 2 || octets == 4) && num > 0 && num <= 2000) {
    size_t end = 3 + static_cast<size_t>(octets) * num;
    if (end <= pn)
      return Bytes(p + 3, p + end);
  }
  return Bytes(p + 3, p + pn);
}

std::vector<Station> parse_mx_nw_stats_cnf(const uint8_t *d, size_t n) {
  std::vector<Station> out;
  size_t off = ETH_HDR + MX_MME_HDR;
  size_t pn = n > off ? n - off : 0;
  const uint8_t *p = d + off;
  if (pn < 1)
    return out;
  // The first record is usually the responder itself (its rated capability,
  // not a link): skip any record carrying the responder's MAC.
  Mac responder = mac_at(d + 6);
  unsigned cnt = p[0];
  size_t q = 1;
  for (unsigned i = 0; i < cnt; i++) {
    if (q + 10 > pn)
      break;
    Mac mac = mac_at(p + q);
    uint16_t tx_raw = le16(p + q + 6);
    uint16_t rx_raw = le16(p + q + 8);
    q += 10;
    if (mac == responder)
      continue;
    Station s;
    s.mac = mac;
    s.tx_rate = decode_phy_rate(tx_raw);
    s.rx_rate = decode_phy_rate(rx_raw);
    out.push_back(s);
  }
  return out;
}

bool parse_mx_status_ind(const uint8_t *d, size_t n, StatusInd &out) {
  size_t off = ETH_HDR + MX_MME_HDR;
  size_t pn = n > off ? n - off : 0;
  const uint8_t *p = d + off;
  if (pn < 8)
    return false;
  out.mac = mac_at(d + 6);
  out.tx_rate = le16(p + 4) * 2;
  out.rx_rate = le16(p + 6) * 2;
  return true;
}

std::vector<Station> parse_qca_nw_stats_cnf(const uint8_t *d, size_t n) {
  std::vector<Station> out;
  size_t off = ETH_HDR + HPAV_MME_HDR + 3;
  if (n < off + 1)
    return out;
  unsigned cnt = d[off];
  off += 1;
  for (unsigned i = 0; i < cnt; i++) {
    if (off + 10 > n)
      break;
    Station s;
    s.mac = mac_at(d + off);
    s.tx_rate = le16(d + off + 6);
    s.rx_rate = le16(d + off + 8);
    off += 10;
    out.push_back(s);
  }
  return out;
}

bool parse_qca_nw_info_cnf(const uint8_t *d, size_t n, int &tx, int &rx) {
  if (n <= ETH_HDR)
    return false;
  const uint8_t *p = d + ETH_HDR;
  size_t pn = n - ETH_HDR;
  if (pn < 8)
    return false;
  uint32_t t = le32(p + pn - 8);
  uint32_t r = le32(p + pn - 4);
  if (t > 5000 || r > 5000 || (t == 0 && r == 0))
    return false;
  tx = static_cast<int>(t * 21 / 16);
  rx = static_cast<int>(r * 21 / 16);
  return true;
}

std::vector<std::pair<Mac, std::pair<int, int>>> parse_qca_nw_info_stations(const uint8_t *d, size_t n,
                                                                          const std::vector<Mac> &peers) {
  std::vector<std::pair<Mac, std::pair<int, int>>> out;
  if (n <= ETH_HDR)
    return out;
  const uint8_t *p = d + ETH_HDR;
  size_t pn = n - ETH_HDR;
  for (const Mac &peer : peers) {
    // rfind: the last occurrence is the station entry
    long pos = -1;
    if (pn >= 6) {
      for (long i = static_cast<long>(pn) - 6; i >= 0; i--) {
        if (std::memcmp(p + i, peer.data(), 6) == 0) {
          pos = i;
          break;
        }
      }
    }
    if (pos < 0 || static_cast<size_t>(pos) + 24 > pn)
      continue;
    unsigned tx = le16(p + pos + 16);
    unsigned rx = le16(p + pos + 20);
    if ((tx || rx) && tx <= 5000 && rx <= 5000)
      out.push_back({peer, {static_cast<int>(tx * 21 / 16), static_cast<int>(rx * 21 / 16)}});
  }
  return out;
}

bool parse_qca_nw_info_cco(const uint8_t *d, size_t n, Mac &out) {
  if (n <= ETH_HDR)
    return false;
  const uint8_t *p = d + ETH_HDR;
  size_t pn = n - ETH_HDR;
  if (pn < 36)
    return false;
  bool all0 = true, allff = true;
  for (int i = 0; i < 6; i++) {
    if (p[30 + i] != 0)
      all0 = false;
    if (p[30 + i] != 0xFF)
      allff = false;
  }
  if (all0 || allff)
    return false;
  out = mac_at(p + 30);
  return true;
}

// ===========================================================================
//  engine
// ===========================================================================

Engine::Engine(Transport *t) : t_(t) { rx_.resize(MAX_FRAME); }

Device &Engine::DevList::setdefault(const Mac &m) {
  for (auto &d : v)
    if (d.mac == m)
      return d;
  Device d;
  d.mac = m;
  v.push_back(d);
  return v.back();
}

Device *Engine::DevList::find(const Mac &m) {
  for (auto &d : v)
    if (d.mac == m)
      return &d;
  return nullptr;
}

uint8_t Engine::next_seq() {
  uint8_t s = seq_;
  seq_ = static_cast<uint8_t>((seq_ % 255) + 1);
  return s;
}

Chip Engine::mac_chipset(const Mac &m) const {
  for (auto &e : chipset_by_mac_)
    if (e.first == m)
      return e.second;
  return Chip::UNKNOWN;
}

void Engine::mark_chipset(const Mac &m, Chip c) {
  for (auto &e : chipset_by_mac_) {
    if (e.first == m) {
      e.second = c;
      return;
    }
  }
  chipset_by_mac_.push_back({m, c});
}

// _send_recv: send, then collect until the deadline. Frames not from
// expected_src are dropped (unicast commands); stop_on ends the wait after the
// first frame with that MMTYPE.
void Engine::send_recv(uint16_t ethertype, const Bytes &frame, uint32_t timeout_ms, const Mac *expected_src,
                       int stop_on, const FrameFn &fn) {
  if (!t_->send(frame.data(), frame.size()))
    tx_failed_ = true;
  uint32_t start = t_->now_ms();
  uint32_t deadline = start + timeout_ms;
  while (static_cast<int32_t>(deadline - t_->now_ms()) > 0) {
    uint32_t remaining = deadline - t_->now_ms();
    size_t n = t_->recv(ethertype, rx_.data(), rx_.size(), std::max<uint32_t>(50, remaining));
    if (n == 0)
      break;  // timeout (socket.timeout in the Python code)
    if (n < ETH_HDR + 3)
      continue;
    uint16_t mmtype = le16(rx_.data() + ETH_HDR + 1);
    Mac src = mac_at(rx_.data() + 6);
    if (expected_src != nullptr && src != *expected_src)
      continue;
    fn(mmtype, src, rx_.data(), n);
    if (stop_on >= 0 && mmtype == stop_on)
      break;
  }
}

// _listen: collect without sending.
void Engine::listen(uint16_t ethertype, uint32_t timeout_ms, const FrameFn &fn) {
  uint32_t deadline = t_->now_ms() + timeout_ms;
  while (static_cast<int32_t>(deadline - t_->now_ms()) > 0) {
    uint32_t remaining = deadline - t_->now_ms();
    size_t n = t_->recv(ethertype, rx_.data(), rx_.size(), std::max<uint32_t>(50, remaining));
    if (n == 0)
      break;
    if (n < ETH_HDR + 3)
      continue;
    uint16_t mmtype = le16(rx_.data() + ETH_HDR + 1);
    Mac src = mac_at(rx_.data() + 6);
    fn(mmtype, src, rx_.data(), n);
  }
}

void Engine::note_link(std::vector<Link> &links, const Mac &responder, const Mac &peer, int tx, int rx) {
  if (responder == peer)
    return;
  if (tx <= 0 && rx <= 0)
    return;
  for (auto &l : links) {
    if (l.src == responder && l.dst == peer) {
      l.tx_rate = tx;
      l.rx_rate = rx;
      return;
    }
  }
  links.push_back({responder, peer, tx, rx});
}

// ---------------------------------------------------------------------------
//  discovery (discovery.py)
// ---------------------------------------------------------------------------

void Engine::discover(double timeout_s, std::vector<Device> &out_devices, std::vector<Link> &links) {
  tx_failed_ = false;
  t_->open(ETHERTYPE_HPAV);
  t_->open(ETHERTYPE_MEDIAXTREAM);
  src_mac_ = t_->own_mac();

  DevList devices;
  links.clear();

  // Step 1: CC_DISCOVER_LIST on 0x88E1 (works on all chipsets)
  {
    Bytes frame = build_hpav_frame(BROADCAST_MAC, src_mac_, CC_DISCOVER_LIST_REQ);
    uint32_t ms = static_cast<uint32_t>(std::min(timeout_s, 3.0) * 1000.0);
    send_recv(ETHERTYPE_HPAV, frame, ms, nullptr, -1, [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
      if (mmtype != CC_DISCOVER_LIST_CNF)
        return;
      devices.setdefault(src);
      for (const Station &sta : parse_discover_cnf(d, n)) {
        Device &dv = devices.setdefault(sta.mac);
        dv.has_same_network = true;
        dv.same_network = sta.same_network;
      }
    });
  }

  // Step 2: MEDIAXTREAM Discover on 0x8912 (Broadcom only)
  {
    Bytes frame = build_mx_frame(BROADCAST_MAC, src_mac_, MX_DISCOVER_REQ, next_seq());
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 2000, nullptr, -1,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != MX_DISCOVER_CNF)
                  return;
                chipset_ = Chip::BROADCOM;
                mark_chipset(src, Chip::BROADCOM);
                Device &dv = devices.setdefault(src);
                MxDiscover info;
                if (parse_mx_discover_cnf(d, n, info)) {
                  if (!info.hfid.empty())
                    dv.model = info.hfid;
                  dv.has_interface = true;
                  dv.interface = info.ethernet ? "ethernet" : "plc";
                }
              });
  }

  fetch_rates(devices, links);        // Step 3
  fetch_device_info(devices);         // Step 4
  fetch_network_roles(devices);       // Step 5

  // _annotate_capabilities
  for (auto &dv : devices.v) {
    Chip cs = mac_chipset(dv.mac);
    dv.chipset = cs != Chip::UNKNOWN ? cs : chipset_;
    dv.cap_vendor_mx = (cs == Chip::BROADCOM || cs == Chip::UNKNOWN);
    dv.cap_vendor_qca = (cs == Chip::QUALCOMM || cs == Chip::UNKNOWN);
    dv.cap_rate_polling = dv.tx_rate > 0 || dv.rx_rate > 0;
  }

  t_->close();
  out_devices = std::move(devices.v);
}

bool Engine::parse_station_rates(const uint8_t *d, size_t n, DevList &devices) {
  size_t off = ETH_HDR + MX_MME_HDR;
  size_t pn = n > off ? n - off : 0;
  const uint8_t *p = d + off;
  bool found = false;
  std::vector<Mac> macs;
  for (auto &dv : devices.v)
    macs.push_back(dv.mac);
  for (const Mac &mac : macs) {
    long idx = -1;
    if (pn >= 6) {
      for (size_t i = 0; i + 6 <= pn; i++) {
        if (std::memcmp(p + i, mac.data(), 6) == 0) {
          idx = static_cast<long>(i);
          break;
        }
      }
    }
    if (idx >= 0 && static_cast<size_t>(idx) + 10 <= pn) {
      unsigned tx = le16(p + idx + 6);
      unsigned rx = le16(p + idx + 8);
      if (tx > 1 && tx < 3000 && rx > 1 && rx < 3000) {
        Device *dv = devices.find(mac);
        dv->tx_rate = static_cast<int>(tx);
        dv->rx_rate = static_cast<int>(rx);
        found = true;
      }
    }
  }
  return found;
}

bool Engine::fetch_rates(DevList &devices, std::vector<Link> &links) {
  bool found = false;

  auto snapshot = [&]() {
    std::vector<Mac> macs;
    for (auto &dv : devices.v)
      macs.push_back(dv.mac);
    return macs;
  };

  // P: passive 0x6046 listening (Broadcom)
  listen(ETHERTYPE_MEDIAXTREAM, 2000, [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
    if (mmtype != MX_STATUS_IND)
      return;
    StatusInd info;
    if (!parse_mx_status_ind(d, n, info))
      return;
    Device &dv = devices.setdefault(info.mac);
    mark_chipset(info.mac, Chip::BROADCOM);
    if (info.tx_rate > 0 || info.rx_rate > 0) {
      dv.tx_rate = info.tx_rate;
      dv.rx_rate = info.rx_rate;
      found = true;
    }
  });
  if (found) {
    chipset_ = Chip::BROADCOM;
    return true;
  }

  // Q: Qualcomm VS_NW_INFO (0xA038)
  bool qca = false;
  for (const Mac &mac : snapshot()) {
    Bytes frame = build_qca_frame(mac, src_mac_, VS_NW_INFO_REQ);
    send_recv(ETHERTYPE_HPAV, frame, 1500, &mac, VS_NW_INFO_CNF,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != VS_NW_INFO_CNF)
                  return;
                chipset_ = Chip::QUALCOMM;
                mark_chipset(src, Chip::QUALCOMM);
                qca = true;
                Mac cco;
                Device *self = devices.find(src);
                if (parse_qca_nw_info_cco(d, n, cco) && self != nullptr) {
                  self->has_cco = true;
                  self->cco_mac = cco;
                }
                std::vector<Mac> peers;
                for (auto &dv : devices.v)
                  if (dv.mac != src)
                    peers.push_back(dv.mac);
                auto per_peer = parse_qca_nw_info_stations(d, n, peers);
                bool have_rates = false;
                int tx = 0, rx = 0;
                if (!per_peer.empty()) {
                  for (auto &pp : per_peer)
                    note_link(links, src, pp.first, pp.second.first, pp.second.second);
                  // fastest link (first one wins a tie, like Python's max())
                  size_t best = 0;
                  for (size_t i = 1; i < per_peer.size(); i++) {
                    int a = per_peer[i].second.first + per_peer[i].second.second;
                    int b = per_peer[best].second.first + per_peer[best].second.second;
                    if (a > b)
                      best = i;
                  }
                  tx = per_peer[best].second.first;
                  rx = per_peer[best].second.second;
                  have_rates = true;
                }
                if (!have_rates)
                  have_rates = parse_qca_nw_info_cnf(d, n, tx, rx);
                if (have_rates && self != nullptr) {
                  self->tx_rate = tx;
                  self->rx_rate = rx;
                  found = true;
                }
              });
  }
  if (qca) {
    bool all_qca = true;
    for (auto &dv : devices.v)
      if (mac_chipset(dv.mac) != Chip::QUALCOMM)
        all_qca = false;
    if (all_qca) {
      if (devices.v.size() == 2) {
        Device &a = devices.v[0];
        Device &b = devices.v[1];
        int ab = a.tx_rate ? a.tx_rate : b.rx_rate;
        int ba = a.rx_rate ? a.rx_rate : b.tx_rate;
        if (ab || ba) {
          a.tx_rate = ab;
          a.rx_rate = ba;
          b.tx_rate = ba;
          b.rx_rate = ab;
          found = true;
        }
      }
      return found;
    }
    // mixed network: fall through to the Broadcom methods
  }

  // Shared handling of one station record from NW_STATS / LINK_STATS / NW_INFO.
  auto apply_station = [&](const Mac &src, const Station &sta, bool mirror) {
    if (sta.tx_rate > 0 || sta.rx_rate > 0) {
      Device &dv = devices.setdefault(sta.mac);
      dv.tx_rate = sta.tx_rate;
      dv.rx_rate = sta.rx_rate;
      if (mirror) {
        // _mirror_link_rate: the responder gets the rate too if it has none
        Device *r = devices.find(src);
        if (r != nullptr && src != sta.mac && r->tx_rate == 0 && r->rx_rate == 0) {
          r->tx_rate = sta.tx_rate;
          r->rx_rate = sta.rx_rate;
        }
      }
      note_link(links, src, sta.mac, sta.tx_rate, sta.rx_rate);
      found = true;
    }
  };

  // A: MX NW_STATS (0xA02C), unicast to each non-QCA adapter
  for (const Mac &mac : snapshot()) {
    if (mac_chipset(mac) == Chip::QUALCOMM)
      continue;
    Bytes frame = build_mx_frame(mac, src_mac_, MX_NW_STATS_REQ, next_seq());
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 2000, nullptr, -1,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != MX_NW_STATS_CNF)
                  return;
                chipset_ = Chip::BROADCOM;
                mark_chipset(src, Chip::BROADCOM);
                for (const Station &sta : parse_mx_nw_stats_cnf(d, n))
                  apply_station(src, sta, true);
              });
  }

  if (!found) {
    Bytes frame = build_mx_frame(BROADCAST_MAC, src_mac_, MX_NW_STATS_REQ, next_seq());
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 3000, nullptr, -1,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != MX_NW_STATS_CNF)
                  return;
                chipset_ = Chip::BROADCOM;
                for (const Station &sta : parse_mx_nw_stats_cnf(d, n))
                  apply_station(src, sta, true);
              });
  }
  if (found)
    return true;

  // B: MX LINK_STATS (0xA032), unicast
  for (const Mac &mac : snapshot()) {
    Bytes frame = build_mx_frame(mac, src_mac_, MX_LINK_STATS_REQ, next_seq());
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 2000, nullptr, -1,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != MX_LINK_STATS_CNF)
                  return;
                chipset_ = Chip::BROADCOM;
                for (const Station &sta : parse_mx_nw_stats_cnf(d, n))
                  apply_station(src, sta, false);
              });
  }
  if (found)
    return true;

  // C: MX GET_STATION_INFO (0xA04C), unicast
  for (const Mac &mac : snapshot()) {
    Bytes frame = build_mx_frame(mac, src_mac_, MX_GET_STATION_REQ, next_seq());
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 2000, nullptr, -1,
              [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
                if (mmtype == MX_GET_STATION_CNF && parse_station_rates(d, n, devices))
                  found = true;
              });
  }
  if (found)
    return true;

  // D: MX NW_INFO (0xA028), unicast; E: broadcast
  static const uint8_t nw_info_payload[2] = {0x00, 0x01};
  auto nw_info_handler = [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
    if (mmtype != MX_NW_INFO_CNF)
      return;
    chipset_ = Chip::BROADCOM;
    NwInfo info = parse_mx_nw_info_cnf(d, n);
    for (const Station &sta : info.stations)
      apply_station(src, sta, false);
  };
  for (const Mac &mac : snapshot()) {
    Bytes frame = build_mx_frame(mac, src_mac_, MX_NW_INFO_REQ, next_seq(), nw_info_payload, 2);
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 2000, nullptr, -1, nw_info_handler);
  }
  if (found)
    return true;
  {
    Bytes frame = build_mx_frame(BROADCAST_MAC, src_mac_, MX_NW_INFO_REQ, next_seq(), nw_info_payload, 2);
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 3000, nullptr, -1, nw_info_handler);
  }
  if (found)
    return true;

  // F: Qualcomm VS_NW_STATS (0xA048) on 0x88E1, broadcast (fallback)
  {
    Bytes frame = build_qca_frame(BROADCAST_MAC, src_mac_, VS_NW_STATS_REQ);
    send_recv(ETHERTYPE_HPAV, frame, 3000, nullptr, -1,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != VS_NW_STATS_CNF)
                  return;
                chipset_ = Chip::QUALCOMM;
                for (const Station &sta : parse_qca_nw_stats_cnf(d, n)) {
                  Device *dv = devices.find(sta.mac);
                  if (dv != nullptr) {
                    dv->tx_rate = sta.tx_rate;
                    dv->rx_rate = sta.rx_rate;
                    note_link(links, src, sta.mac, sta.tx_rate, sta.rx_rate);
                    found = true;
                  }
                }
              });
  }
  return found;
}

void Engine::fetch_network_roles(DevList &devices) {
  static const uint8_t payload[2] = {0x00, 0x01};
  std::vector<Mac> macs;
  for (auto &dv : devices.v)
    macs.push_back(dv.mac);
  for (const Mac &mac : macs) {
    if (mac_chipset(mac) == Chip::QUALCOMM)
      continue;
    if (std::find(roles_attempted_.begin(), roles_attempted_.end(), mac) != roles_attempted_.end())
      continue;
    roles_attempted_.push_back(mac);
    Bytes frame = build_mx_frame(mac, src_mac_, MX_NW_INFO_REQ, next_seq(), payload, 2);
    send_recv(ETHERTYPE_MEDIAXTREAM, frame, 1500, nullptr, -1,
              [&](uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n) {
                if (mmtype != MX_NW_INFO_CNF)
                  return;
                NwInfo info = parse_mx_nw_info_cnf(d, n);
                for (const Network &nw : info.networks) {
                  static const Mac zero = {0, 0, 0, 0, 0, 0};
                  if (nw.cco_mac == zero)
                    continue;
                  bool set = false;
                  for (auto &e : cco_by_mac_) {
                    if (e.first == src) {
                      e.second = nw.cco_mac;
                      set = true;
                    }
                  }
                  if (!set)
                    cco_by_mac_.push_back({src, nw.cco_mac});
                }
              });
  }
  for (auto &dv : devices.v) {
    for (auto &e : cco_by_mac_) {
      if (e.first == dv.mac) {
        dv.has_cco = true;
        dv.cco_mac = e.second;
      }
    }
  }
}

void Engine::fetch_device_info(DevList &devices) {
  std::vector<Mac> macs;
  for (auto &dv : devices.v)
    macs.push_back(dv.mac);
  for (const Mac &mac : macs) {
    Chip cs = mac_chipset(mac);

    if (cs == Chip::BROADCOM || cs == Chip::UNKNOWN) {
      // MX Get Parameter: manufacturer HFID -> model
      if (devices.find(mac)->model.empty()) {
        uint8_t pl[2] = {PARAM_MANUFACTURER_HFID & 0xFF, PARAM_MANUFACTURER_HFID >> 8};
        Bytes frame = build_mx_frame(mac, src_mac_, MX_GET_PARAM_REQ, next_seq(), pl, 2);
        send_recv(ETHERTYPE_MEDIAXTREAM, frame, 1500, nullptr, -1,
                  [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
                    if (mmtype != MX_GET_PARAM_CNF)
                      return;
                    Bytes val = parse_mx_get_param_cnf(d, n);
                    std::string s = strip_nul_then_ws(ascii_ignore(val.data(), val.size()));
                    if (!s.empty())
                      devices.find(mac)->model = s;
                  });
      }
      // MX Get Parameter: user HFID -> firmware/name
      if (devices.find(mac)->firmware_ver.empty()) {
        uint8_t pl[2] = {PARAM_USER_HFID & 0xFF, PARAM_USER_HFID >> 8};
        Bytes frame = build_mx_frame(mac, src_mac_, MX_GET_PARAM_REQ, next_seq(), pl, 2);
        send_recv(ETHERTYPE_MEDIAXTREAM, frame, 1500, nullptr, -1,
                  [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
                    if (mmtype != MX_GET_PARAM_CNF)
                      return;
                    Bytes val = parse_mx_get_param_cnf(d, n);
                    std::string s = strip_nul_then_ws(ascii_ignore(val.data(), val.size()));
                    if (!s.empty())
                      devices.find(mac)->firmware_ver = s;
                  });
      }
    }

    if (cs == Chip::QUALCOMM || cs == Chip::UNKNOWN) {
      if (devices.find(mac)->firmware_ver.empty()) {
        Bytes frame = build_qca_frame(mac, src_mac_, VS_SW_VER_REQ);
        send_recv(ETHERTYPE_HPAV, frame, 1500, nullptr, -1,
                  [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
                    if (mmtype != VS_SW_VER_CNF)
                      return;
                    size_t off = ETH_HDR + HPAV_MME_HDR + 3;
                    if (n > off + 3 && d[off] == 0) {
                      size_t ver_len = d[off + 2];
                      size_t avail = n - (off + 3);
                      devices.find(mac)->firmware_ver =
                          rstrip_nul(ascii_ignore(d + off + 3, std::min(ver_len, avail)));
                    }
                  });
      }
    }
  }
}

// ---------------------------------------------------------------------------
//  state queries (state.py, pib.py read path, av1300.py read path)
// ---------------------------------------------------------------------------

// Module-op read (0xA0B0, op 0x0100) of one PIB chunk.
bool Engine::qca_read_chunk(const Mac &mac, uint32_t offset, uint32_t clen, Bytes &out) {
  // _QCA_HDR_READ[:21] with the length at 17 and the offset at 19 patched in
  uint8_t hdr[21] = {0, 0, 0, 0, 0x01, 0, 0, 0x12, 0, 0, 0, 0, 0, 0x02, 0x70, 0, 0, 0x78, 0x05, 0, 0};
  hdr[17] = static_cast<uint8_t>(clen & 0xFF);
  hdr[18] = static_cast<uint8_t>((clen >> 8) & 0xFF);
  hdr[19] = static_cast<uint8_t>(offset & 0xFF);
  hdr[20] = static_cast<uint8_t>((offset >> 8) & 0xFF);
  Bytes frame = build_qca_vs0_frame(mac, src_mac_, VS_MOD_OP_REQ, hdr, sizeof(hdr));
  bool got = false;
  send_recv(ETHERTYPE_HPAV, frame, 2000, &mac, VS_MOD_OP_CNF,
            [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
              if (mmtype != VS_MOD_OP_CNF || got)
                return;
              const uint8_t *pl = d + ETH_HDR + 6;  // after MMV + MMTYPE + OUI
              size_t pn = n - (ETH_HDR + 6);
              if (pn < 27)
                return;
              if (le32(pl + 23) != offset)
                return;
              size_t take = std::min<size_t>(clen, pn - 27);
              out.assign(pl + 27, pl + 27 + take);
              got = true;
            });
  return got && !out.empty();
}

bool Engine::pib_readable(const Mac &mac, uint32_t offset) {
  Bytes chunk;
  return qca_read_chunk(mac, offset, 16, chunk) && chunk.size() == 16;
}

uint32_t Engine::pib_size(const Mac &mac) {
  for (auto &e : pib_size_by_mac_)
    if (e.first == mac)
      return e.second;
  uint32_t size = QCA_PIB_SIZE;
  for (uint32_t candidate : {AV1300_PIB_SIZE, AVM_PIB_SIZE}) {
    if (candidate <= QCA_PIB_SIZE)
      continue;
    // accepted only if a read just below succeeds and a read at it fails
    bool bigger = pib_readable(mac, candidate - 16) && !pib_readable(mac, candidate);
    if (bigger) {
      size = candidate;
      break;
    }
  }
  pib_size_by_mac_.push_back({mac, size});
  return size;
}

// Reads the whole PIB in chunks (so a read failure anywhere aborts, as in
// Python) but keeps only the bytes at the requested offsets: the 22 KB AV1300
// PIB never has to fit into RAM.
bool Engine::read_pib_samples(const Mac &mac, uint32_t size, std::vector<std::pair<uint32_t, uint8_t>> &samples) {
  uint32_t offset = 0;
  Bytes chunk;
  while (offset < size) {
    uint32_t clen = std::min(QCA_PIB_CHUNK, size - offset);
    chunk.clear();
    if (!qca_read_chunk(mac, offset, clen, chunk) || chunk.size() < clen)
      return false;
    for (auto &s : samples)
      if (s.first >= offset && s.first < offset + clen)
        s.second = chunk[s.first - offset];
    offset += clen;
  }
  return true;
}

bool Engine::get_param_value(const Mac &mac, uint16_t param_id, Bytes &out) {
  uint8_t pl[2] = {static_cast<uint8_t>(param_id & 0xFF), static_cast<uint8_t>(param_id >> 8)};
  Bytes frame = build_mx_frame(mac, src_mac_, MX_GET_PARAM_REQ, next_seq(), pl, 2);
  bool got = false;
  send_recv(ETHERTYPE_MEDIAXTREAM, frame, 1500, &mac, MX_GET_PARAM_CNF,
            [&](uint16_t mmtype, const Mac &, const uint8_t *d, size_t n) {
              if (mmtype != MX_GET_PARAM_CNF || got)
                return;
              Bytes val = parse_mx_get_param_cnf(d, n);
              if (!val.empty()) {
                out = std::move(val);
                got = true;
              }
            });
  return got;
}

static const uint32_t QCA_LED_OFFSETS[10] = {0x1ED3, 0x1EFB, 0x1F03, 0x1F1B, 0x1F23,
                                             0x1F2B, 0x1F43, 0x1F4B, 0x1F53, 0x1F6B};
static const uint32_t AV1300_LED_OFFSETS[11] = {0x255F, 0x2587, 0x258F, 0x2597, 0x259F, 0x25A7,
                                                0x25AF, 0x25B7, 0x25BF, 0x25C7, 0x25CF};
static const uint32_t QCA_QOS_OFFSET = 0x0ADC;
static const uint32_t QCA_POWERSAVE_PROBE = 0x21EA;
static const struct {
  uint32_t off;
  uint8_t on;
} AV1300_POWERSAVE[5] = {{0x27D5, 0x08}, {0x27D6, 0x96}, {0x287E, 0x01}, {0x28F8, 0x01}, {0x2907, 0x02}};

std::vector<std::pair<std::string, DeviceState>> Engine::query_states(const std::vector<std::string> &macs) {
  tx_failed_ = false;
  std::vector<std::pair<std::string, DeviceState>> states;
  std::vector<std::pair<std::string, Mac>> qca_macs, mx_macs;
  for (const std::string &s : macs) {
    bool dup = false;
    for (auto &e : states)
      if (e.first == s)
        dup = true;
    if (dup)
      continue;
    states.push_back({s, DeviceState()});
    Mac m;
    if (!mac_from_str(s, m))
      continue;  // callers validate first; never reached from the HTTP layer
    (mac_chipset(m) == Chip::QUALCOMM ? qca_macs : mx_macs).push_back({s, m});
  }
  auto state_of = [&](const std::string &key) -> DeviceState & {
    for (auto &e : states)
      if (e.first == key)
        return e.second;
    return states.front().second;  // unreachable
  };

  src_mac_ = t_->own_mac();

  // Qualcomm: read the real state straight from the PIB.
  if (!qca_macs.empty()) {
    t_->open(ETHERTYPE_HPAV);
    for (auto &e : qca_macs) {
      const Mac &mac = e.second;
      DeviceState &st = state_of(e.first);
      uint32_t size = pib_size(mac);
      bool av1300 = size == AV1300_PIB_SIZE;

      std::vector<std::pair<uint32_t, uint8_t>> samples;
      if (av1300) {
        for (uint32_t o : AV1300_LED_OFFSETS)
          samples.push_back({o, 0});
        for (auto &p : AV1300_POWERSAVE)
          samples.push_back({p.off, 0});
      } else {
        for (uint32_t o : QCA_LED_OFFSETS)
          samples.push_back({o, 0});
        samples.push_back({QCA_POWERSAVE_PROBE, 0});
      }
      samples.push_back({QCA_QOS_OFFSET, 0});
      samples.push_back({QCA_QOS_OFFSET + 1, 0});
      if (!read_pib_samples(mac, size, samples))
        continue;
      auto at = [&](uint32_t off) -> uint8_t {
        for (auto &s : samples)
          if (s.first == off)
            return s.second;
        return 0;
      };

      if (av1300) {
        // led_state_av1300: all 0x00 = on, all 0x01 = off, else unknown
        bool all0 = true, all1 = true;
        for (uint32_t o : AV1300_LED_OFFSETS) {
          if (at(o) != 0x00)
            all0 = false;
          if (at(o) != 0x01)
            all1 = false;
        }
        st.led = all0 ? Tri::TRUE_ : (all1 ? Tri::FALSE_ : Tri::NONE);
      } else {
        bool plausible = true;
        for (uint32_t o : QCA_LED_OFFSETS)
          if (at(o) != 0x00 && at(o) != 0x01)
            plausible = false;
        if (plausible)
          st.led = at(QCA_LED_OFFSETS[0]) == 0x00 ? Tri::TRUE_ : Tri::FALSE_;
      }

      unsigned qv = at(QCA_QOS_OFFSET) | (at(QCA_QOS_OFFSET + 1) << 8);
      if (qv == 0x0000)
        st.qos = "internet";
      else if (qv == 0xFA41)
        st.qos = "gaming";
      else if (qv == 0xFA42)
        st.qos = "audio_video";
      else if (qv == 0xFA43)
        st.qos = "voip";

      if (av1300) {
        // power_saving_state_av1300: all "on" values = on, all zero = off
        bool all_on = true, all_off = true;
        for (auto &p : AV1300_POWERSAVE) {
          if (at(p.off) != p.on)
            all_on = false;
          if (at(p.off) != 0x00)
            all_off = false;
        }
        st.power_saving = all_on ? Tri::TRUE_ : (all_off ? Tri::FALSE_ : Tri::NONE);
      } else {
        st.power_saving = at(QCA_POWERSAVE_PROBE) == 0x01 ? Tri::TRUE_ : Tri::FALSE_;
      }
    }
    t_->close();
  }

  // Broadcom (and not yet identified): MEDIAXTREAM Get Parameter.
  if (!mx_macs.empty()) {
    t_->open(ETHERTYPE_MEDIAXTREAM);
    for (auto &e : mx_macs) {
      const Mac &mac = e.second;
      DeviceState &st = state_of(e.first);
      Bytes v;
      if (get_param_value(mac, PARAM_LED_OPTIONS, v) && v.size() >= 4)
        st.led = (v[3] & 0x10) ? Tri::TRUE_ : Tri::FALSE_;
      v.clear();
      if (get_param_value(mac, PARAM_POWER_STANDBY, v) && v.size() >= 2)
        st.power_saving = (le16(v.data()) & 0x8000) ? Tri::TRUE_ : Tri::FALSE_;
      v.clear();
      static const size_t cap_offsets[8] = {2, 27, 52, 77, 102, 127, 152, 177};
      static const struct {
        const char *mode;
        uint8_t caps[8];
      } cap_map[4] = {
          {"internet", {0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18}},
          {"audio_video", {0x58, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78}},
          {"gaming", {0x38, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78}},
          {"voip", {0x78, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78}},
      };
      if (get_param_value(mac, PARAM_QOS_PRIORITY_MAP, v) && v.size() > cap_offsets[7]) {
        for (auto &m : cap_map) {
          bool eq = true;
          for (int i = 0; i < 8; i++)
            if (v[cap_offsets[i]] != m.caps[i])
              eq = false;
          if (eq) {
            st.qos = m.mode;
            break;
          }
        }
      }
    }
    t_->close();
  }
  return states;
}

// ---------------------------------------------------------------------------
//  JSON
// ---------------------------------------------------------------------------

static void json_str(std::string &o, const std::string &s) {
  // json.dumps(ensure_ascii=True)
  o.push_back('"');
  for (unsigned char c : s) {
    switch (c) {
      case '"':
        o += "\\\"";
        break;
      case '\\':
        o += "\\\\";
        break;
      case '\n':
        o += "\\n";
        break;
      case '\r':
        o += "\\r";
        break;
      case '\t':
        o += "\\t";
        break;
      case '\b':
        o += "\\b";
        break;
      case '\f':
        o += "\\f";
        break;
      default:
        if (c < 0x20 || c > 0x7F) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          o += buf;
        } else {
          o.push_back(static_cast<char>(c));
        }
    }
  }
  o.push_back('"');
}

static void json_bool(std::string &o, bool b) { o += b ? "true" : "false"; }

std::string Engine::info_json(const std::string &interface) {
  std::string o = "{\"probe\": \"powerline\", \"version\": \"1\", \"interface\": ";
  json_str(o, interface);
  o += "}";
  return o;
}

std::string Engine::discover_json(const std::vector<Device> &devices, const std::vector<Link> &links) {
  std::string o = "{\"devices\": [";
  bool first = true;
  for (const Device &d : devices) {
    if (!first)
      o += ", ";
    first = false;
    std::string mac = mac_to_str(d.mac);
    o += "{\"mac\": ";
    json_str(o, mac);
    o += ", \"plcmac\": ";
    json_str(o, mac);
    o += ", \"model\": ";
    json_str(o, d.model);
    o += ", \"firmware_ver\": ";
    json_str(o, d.firmware_ver);
    o += ", \"tx_rate\": " + std::to_string(d.tx_rate);
    o += ", \"rx_rate\": " + std::to_string(d.rx_rate);
    if (d.has_same_network) {
      o += ", \"same_network\": ";
      json_bool(o, d.same_network);
    }
    if (d.has_interface) {
      o += ", \"_interface\": ";
      json_str(o, d.interface);
    }
    if (d.has_cco) {
      o += ", \"cco_mac\": ";
      json_str(o, mac_to_str(d.cco_mac));
    }
    o += ", \"chipset\": ";
    json_str(o, chip_name(d.chipset));
    o += ", \"capabilities\": {\"supports_standard_discovery\": true, \"supports_vendor_mx\": ";
    json_bool(o, d.cap_vendor_mx);
    o += ", \"supports_vendor_qca\": ";
    json_bool(o, d.cap_vendor_qca);
    o += ", \"supports_rate_polling\": ";
    json_bool(o, d.cap_rate_polling);
    o += ", \"supports_led_control\": false}}";
  }
  o += "], \"plc_links\": [";
  first = true;
  for (const Link &l : links) {
    if (!first)
      o += ", ";
    first = false;
    o += "[";
    json_str(o, mac_to_str(l.src));
    o += ", ";
    json_str(o, mac_to_str(l.dst));
    o += ", {\"tx_rate\": " + std::to_string(l.tx_rate) + ", \"rx_rate\": " + std::to_string(l.rx_rate) + "}]";
  }
  o += "]}";
  return o;
}

std::string Engine::states_json(const std::vector<std::pair<std::string, DeviceState>> &states) {
  std::string o = "{";
  bool first = true;
  for (auto &e : states) {
    if (!first)
      o += ", ";
    first = false;
    json_str(o, e.first);
    o += ": {\"led\": ";
    o += e.second.led == Tri::NONE ? "null" : (e.second.led == Tri::TRUE_ ? "true" : "false");
    o += ", \"qos\": ";
    if (e.second.qos == nullptr)
      o += "null";
    else
      json_str(o, e.second.qos);
    o += ", \"power_saving\": ";
    o += e.second.power_saving == Tri::NONE ? "null" : (e.second.power_saving == Tri::TRUE_ ? "true" : "false");
    o += "}";
  }
  o += "}";
  return o;
}

}  // namespace hp
}  // namespace powerline_probe
}  // namespace esphome
