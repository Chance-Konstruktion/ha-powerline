// HomePlug AV read-only engine: a C++ port of the integration's
// custom_components/powerline/homeplug (discovery.py, state.py, parsers.py,
// frames.py, pib.py read path, av1300.py read path).
//
// No ESP-IDF / ESPHome includes in here on purpose: the engine talks to the
// wire through the small Transport interface, so the same code is compiled
// for the ESP32 and, in probe/esphome/tests, on the host against a scripted
// transport and checked against the Python implementation.
//
// Read-only: nothing in this file builds a frame that writes to an adapter.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace esphome {
namespace powerline_probe {
namespace hp {

constexpr uint16_t ETHERTYPE_HPAV = 0x88E1;
constexpr uint16_t ETHERTYPE_MEDIAXTREAM = 0x8912;
constexpr size_t ETH_HDR = 14;
constexpr size_t HPAV_MME_HDR = 5;
constexpr size_t MX_MME_HDR = 9;
constexpr size_t ETH_MIN = 60;
// Largest frame we ever receive (a 1400 byte PIB chunk plus headers).
constexpr size_t MAX_FRAME = 1536;

using Mac = std::array<uint8_t, 6>;
constexpr Mac BROADCAST_MAC = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ---- the wire -------------------------------------------------------------
class Transport {
 public:
  virtual ~Transport() = default;
  // Start capturing frames of this EtherType (drops anything queued before),
  // like opening a fresh raw socket in the Python code.
  virtual void open(uint16_t ethertype) = 0;
  // Stop capturing and drop what is queued (closing the sockets).
  virtual void close() = 0;
  // Transmit one complete Ethernet frame. False on failure.
  virtual bool send(const uint8_t *frame, size_t len) = 0;
  // Next captured frame of this EtherType, waiting up to timeout_ms.
  // Returns its length (0 = timed out). The frame is copied into buf.
  virtual size_t recv(uint16_t ethertype, uint8_t *buf, size_t cap, uint32_t timeout_ms) = 0;
  virtual uint32_t now_ms() = 0;
  virtual Mac own_mac() = 0;
};

// ---- constants (const.py) -------------------------------------------------
constexpr uint16_t CC_DISCOVER_LIST_REQ = 0x0014, CC_DISCOVER_LIST_CNF = 0x0015;
constexpr uint16_t MX_DISCOVER_REQ = 0xA070, MX_DISCOVER_CNF = 0xA071;
constexpr uint16_t MX_NW_INFO_REQ = 0xA028, MX_NW_INFO_CNF = 0xA029;
constexpr uint16_t MX_NW_STATS_REQ = 0xA02C, MX_NW_STATS_CNF = 0xA02D;
constexpr uint16_t MX_GET_PARAM_REQ = 0xA05C, MX_GET_PARAM_CNF = 0xA05D;
constexpr uint16_t MX_LINK_STATS_REQ = 0xA032, MX_LINK_STATS_CNF = 0xA033;
constexpr uint16_t MX_GET_STATION_REQ = 0xA04C, MX_GET_STATION_CNF = 0xA04D;
constexpr uint16_t MX_STATUS_IND = 0x6046;
constexpr uint16_t VS_SW_VER_REQ = 0xA000, VS_SW_VER_CNF = 0xA001;
constexpr uint16_t VS_NW_INFO_REQ = 0xA038, VS_NW_INFO_CNF = 0xA039;
constexpr uint16_t VS_NW_STATS_REQ = 0xA048, VS_NW_STATS_CNF = 0xA049;
constexpr uint16_t VS_MOD_OP_REQ = 0xA0B0, VS_MOD_OP_CNF = 0xA0B1;

constexpr uint16_t PARAM_MANUFACTURER_HFID = 0x0001;
constexpr uint16_t PARAM_USER_HFID = 0x0025;
constexpr uint16_t PARAM_POWER_STANDBY = 0x0029;
constexpr uint16_t PARAM_LED_OPTIONS = 0x003F;
constexpr uint16_t PARAM_QOS_PRIORITY_MAP = 0x0069;

constexpr uint32_t QCA_PIB_SIZE = 0x2370;   // generic AV500 PIB, 9072 bytes
constexpr uint32_t QCA_PIB_CHUNK = 0x0578;  // 1400 byte read chunks
constexpr uint32_t AVM_PIB_SIZE = 0x2644;   // FRITZ!Powerline, 9796 bytes
constexpr uint32_t AV1300_PIB_SIZE = 0x5748;  // TL-WPA8631P / TL-PA8010P, 22344 bytes

// ---- frames (frames.py) ---------------------------------------------------
using Bytes = std::vector<uint8_t>;

std::string mac_to_str(const uint8_t *b);
inline std::string mac_to_str(const Mac &m) { return mac_to_str(m.data()); }
// Accepts "AA:BB:CC:DD:EE:FF", "AA-BB-..", "AABBCCDDEEFF"; exactly six bytes.
bool mac_from_str(const std::string &s, Mac &out);

Bytes build_hpav_frame(const Mac &dst, const Mac &src, uint16_t mmtype, const uint8_t *payload = nullptr,
                       size_t n = 0);
Bytes build_qca_frame(const Mac &dst, const Mac &src, uint16_t mmtype, const uint8_t *payload = nullptr,
                      size_t n = 0);
Bytes build_qca_vs0_frame(const Mac &dst, const Mac &src, uint16_t mmtype, const uint8_t *payload, size_t n);
Bytes build_mx_frame(const Mac &dst, const Mac &src, uint16_t mmtype, uint8_t seq, const uint8_t *payload = nullptr,
                     size_t n = 0);

// ---- parsers (parsers.py) -------------------------------------------------
struct Station {
  Mac mac;
  int tx_rate = 0;
  int rx_rate = 0;
  bool same_network = true;
};

struct Network {
  Mac cco_mac;
};

struct NwInfo {
  std::vector<Network> networks;
  std::vector<Station> stations;
};

struct MxDiscover {
  bool ethernet = false;  // "ethernet" else "plc"
  std::string hfid;
};

struct StatusInd {
  Mac mac;
  int tx_rate = 0;
  int rx_rate = 0;
};

int decode_phy_rate(uint16_t raw);
std::vector<Station> parse_discover_cnf(const uint8_t *d, size_t n);
bool parse_mx_discover_cnf(const uint8_t *d, size_t n, MxDiscover &out);
NwInfo parse_mx_nw_info_cnf(const uint8_t *d, size_t n);
Bytes parse_mx_get_param_cnf(const uint8_t *d, size_t n);
std::vector<Station> parse_mx_nw_stats_cnf(const uint8_t *d, size_t n);
bool parse_mx_status_ind(const uint8_t *d, size_t n, StatusInd &out);
std::vector<Station> parse_qca_nw_stats_cnf(const uint8_t *d, size_t n);
bool parse_qca_nw_info_cnf(const uint8_t *d, size_t n, int &tx, int &rx);
std::vector<std::pair<Mac, std::pair<int, int>>> parse_qca_nw_info_stations(const uint8_t *d, size_t n,
                                                                          const std::vector<Mac> &peers);
bool parse_qca_nw_info_cco(const uint8_t *d, size_t n, Mac &out);

// ---- engine ---------------------------------------------------------------
enum class Chip : uint8_t { UNKNOWN, BROADCOM, QUALCOMM };
const char *chip_name(Chip c);

struct Device {
  Mac mac{};
  std::string model;
  std::string firmware_ver;
  int tx_rate = 0;
  int rx_rate = 0;
  bool has_same_network = false;
  bool same_network = false;
  bool has_interface = false;
  std::string interface;  // "ethernet" / "plc" / ""
  bool has_cco = false;
  Mac cco_mac{};
  Chip chipset = Chip::UNKNOWN;  // filled in by annotate
  bool cap_vendor_mx = false;
  bool cap_vendor_qca = false;
  bool cap_rate_polling = false;
};

struct Link {
  Mac src, dst;
  int tx_rate, rx_rate;
};

enum class Tri : uint8_t { NONE, FALSE_, TRUE_ };

struct DeviceState {
  Tri led = Tri::NONE;
  const char *qos = nullptr;  // nullptr = null
  Tri power_saving = Tri::NONE;
};

class Engine {
 public:
  explicit Engine(Transport *t);

  // Runs the full discovery (/v1/discover). timeout is clamped by the caller.
  void discover(double timeout_s, std::vector<Device> &devices, std::vector<Link> &links);
  // /v1/states. macs must already be uppercase strings; returns {mac: state}
  // in the order given (duplicates collapsed).
  std::vector<std::pair<std::string, DeviceState>> query_states(const std::vector<std::string> &macs);

  // True if a frame could not be transmitted during the last call (the
  // Python code would have raised OSError there -> HTTP 500).
  bool tx_failed() const { return tx_failed_; }

  // Serializers producing the probe's JSON.
  static std::string discover_json(const std::vector<Device> &devices, const std::vector<Link> &links);
  static std::string states_json(const std::vector<std::pair<std::string, DeviceState>> &states);
  static std::string info_json(const std::string &interface);

 private:
  using FrameFn = std::function<void(uint16_t mmtype, const Mac &src, const uint8_t *d, size_t n)>;

  struct DevList {
    std::vector<Device> v;
    Device &setdefault(const Mac &m);
    Device *find(const Mac &m);
  };

  uint8_t next_seq();
  Chip mac_chipset(const Mac &m) const;
  void mark_chipset(const Mac &m, Chip c);

  void send_recv(uint16_t ethertype, const Bytes &frame, uint32_t timeout_ms, const Mac *expected_src, int stop_on,
                 const FrameFn &fn);
  void listen(uint16_t ethertype, uint32_t timeout_ms, const FrameFn &fn);

  void note_link(std::vector<Link> &links, const Mac &responder, const Mac &peer, int tx, int rx);
  bool fetch_rates(DevList &devices, std::vector<Link> &links);
  bool parse_station_rates(const uint8_t *d, size_t n, DevList &devices);
  void fetch_network_roles(DevList &devices);
  void fetch_device_info(DevList &devices);

  // PIB read path (pib.py / av1300.py)
  bool qca_read_chunk(const Mac &mac, uint32_t offset, uint32_t clen, Bytes &out);
  bool pib_readable(const Mac &mac, uint32_t offset);
  uint32_t pib_size(const Mac &mac);
  bool read_pib_samples(const Mac &mac, uint32_t size, std::vector<std::pair<uint32_t, uint8_t>> &samples);
  bool get_param_value(const Mac &mac, uint16_t param_id, Bytes &out);

  Transport *t_;
  Mac src_mac_{};
  uint8_t seq_ = 1;
  Chip chipset_ = Chip::UNKNOWN;
  std::vector<std::pair<Mac, Chip>> chipset_by_mac_;
  std::vector<Mac> roles_attempted_;
  std::vector<std::pair<Mac, Mac>> cco_by_mac_;
  std::vector<std::pair<Mac, uint32_t>> pib_size_by_mac_;
  Bytes rx_;
  bool tx_failed_ = false;
};

}  // namespace hp
}  // namespace powerline_probe
}  // namespace esphome
