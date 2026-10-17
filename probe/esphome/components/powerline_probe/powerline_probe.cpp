#include "powerline_probe.h"

#ifdef USE_ESP32

#include <esp_timer.h>
#include <esp_vfs_l2tap.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <cstring>

#include "esphome/components/ethernet/ethernet_component.h"
#include "esphome/core/log.h"

namespace esphome {
namespace powerline_probe {

static const char *const TAG = "powerline_probe";

// Same bounds and version as probe/powerline_probe.py
static const char *const PROBE_VERSION = "1";
static const double MIN_TIMEOUT = 1.0;
static const double MAX_TIMEOUT = 15.0;
static const double DEFAULT_TIMEOUT = 5.0;

// ===========================================================================
//  L2 TAP transport
// ===========================================================================
//
// Why L2 TAP and not the lwIP hook
// --------------------------------
// The obvious way to get non-IP frames out of lwIP is
// LWIP_HOOK_UNKNOWN_ETH_PROTOCOL(pbuf, netif) (ethernet_input(): an ERR_OK
// return means the hook consumed and freed the pbuf, anything else makes lwIP
// count and free it). ESP-IDF 5.5 (the version ESPHome 2026.9 builds with)
// has no Kconfig switch for it, though: lwip/port/include/lwipopts.h only
// wires the hooks listed in lwip/Kconfig, and "UNKNOWN_ETH_PROTOCOL" is not
// among them. The remaining route, a header named by
// ESP_IDF_LWIP_HOOK_FILENAME, needs a -D flag on every IDF component that
// includes lwIP, which an ESPHome external component cannot set cleanly.
//
// ESP-IDF's L2 TAP is the supported way to do the same thing: with
// CONFIG_ESP_NETIF_L2_TAP the Ethernet glue's input path (esp_eth_netif_glue.c
// eth_input_to_netif) offers every received frame to the open TAP descriptors
// first and only passes it on to lwIP when none of them wants it. A descriptor
// is bound to the Ethernet driver and an EtherType filter, read() returns whole
// frames, write() transmits one with esp_eth_transmit() (guarded by
// ETH_TRANSMIT_MUTEX, which L2 TAP selects), so neither a tcpip-core lock nor
// pbufs are needed. Frames of other EtherTypes are untouched.
//
// Costs: one heap buffer per queued frame (the driver's RX buffer, at most
// CONFIG_ESP_NETIF_L2_TAP_RX_QUEUE_SIZE per descriptor), and a descriptor
// exists only while a request runs, like the raw sockets of the Python probe.

bool L2tapTransport::init(esp_netif_t *netif) {
  this->netif_ = netif;
  esp_err_t err = esp_vfs_l2tap_intf_register(nullptr);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(TAG, "L2 TAP registration failed: %s", esp_err_to_name(err));
    return false;
  }
  return true;
}

int &L2tapTransport::fd_for_(uint16_t ethertype) { return ethertype == hp::ETHERTYPE_HPAV ? this->fd_hpav_ : this->fd_mx_; }

void L2tapTransport::open(uint16_t ethertype) {
  int &slot = this->fd_for_(ethertype);
  if (slot >= 0) {
    ::close(slot);  // fresh descriptor == empty queue, like re-opening a socket
    slot = -1;
  }
  int fd = ::open(L2TAP_VFS_DEFAULT_PATH, O_NONBLOCK);
  if (fd < 0) {
    ESP_LOGW(TAG, "open(%s) failed: errno %d", L2TAP_VFS_DEFAULT_PATH, errno);
    return;
  }
  l2tap_iodriver_handle driver = esp_netif_get_io_driver(this->netif_);
  uint16_t filter = ethertype;
  if (::ioctl(fd, L2TAP_S_DEVICE_DRV_HNDL, driver) < 0 || ::ioctl(fd, L2TAP_S_RCV_FILTER, &filter) < 0) {
    ESP_LOGW(TAG, "L2 TAP setup for 0x%04X failed: errno %d", ethertype, errno);
    ::close(fd);
    return;
  }
  slot = fd;
}

void L2tapTransport::close() {
  for (int *slot : {&this->fd_hpav_, &this->fd_mx_}) {
    if (*slot >= 0) {
      ::close(*slot);
      *slot = -1;
    }
  }
}

bool L2tapTransport::send(const uint8_t *frame, size_t len) {
  if (len < hp::ETH_HDR)
    return false;
  uint16_t ethertype = static_cast<uint16_t>((frame[12] << 8) | frame[13]);
  int fd = this->fd_for_(ethertype);
  if (fd < 0)
    return false;
  ssize_t n = ::write(fd, frame, len);
  if (n != static_cast<ssize_t>(len)) {
    ESP_LOGD(TAG, "TX 0x%04X failed: errno %d", ethertype, errno);
    return false;
  }
  return true;
}

size_t L2tapTransport::recv(uint16_t ethertype, uint8_t *buf, size_t cap, uint32_t timeout_ms) {
  int fd = this->fd_for_(ethertype);
  if (fd < 0) {
    vTaskDelay(pdMS_TO_TICKS(timeout_ms));
    return 0;
  }
  const uint32_t deadline = this->now_ms() + timeout_ms;
  while (true) {
    ssize_t n = ::read(fd, buf, cap);  // O_NONBLOCK: -1/EAGAIN when the queue is empty
    if (n > 0)
      return static_cast<size_t>(n);
    int32_t remaining = static_cast<int32_t>(deadline - this->now_ms());
    if (remaining <= 0)
      return 0;
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(fd, &rfds);
    struct timeval tv;
    tv.tv_sec = remaining / 1000;
    tv.tv_usec = (remaining % 1000) * 1000;
    if (::select(fd + 1, &rfds, nullptr, nullptr, &tv) < 0)
      vTaskDelay(1);  // never spin on a persistent select() error
  }
}

uint32_t L2tapTransport::now_ms() { return static_cast<uint32_t>(esp_timer_get_time() / 1000); }

hp::Mac L2tapTransport::own_mac() {
  hp::Mac mac{};
  if (esp_netif_get_mac(this->netif_, mac.data()) != ESP_OK)
    ESP_LOGW(TAG, "cannot read the Ethernet MAC address");
  return mac;
}

// ===========================================================================
//  HTTP
// ===========================================================================

static std::string percent_decode(const std::string &in) {
  // urllib.parse.parse_qs: %XX and '+' -> ' '
  std::string out;
  for (size_t i = 0; i < in.size(); i++) {
    char c = in[i];
    if (c == '+') {
      out.push_back(' ');
    } else if (c == '%' && i + 2 < in.size() && std::isxdigit(static_cast<unsigned char>(in[i + 1])) &&
               std::isxdigit(static_cast<unsigned char>(in[i + 2]))) {
      out.push_back(static_cast<char>(std::strtol(in.substr(i + 1, 2).c_str(), nullptr, 16)));
      i += 2;
    } else {
      out.push_back(c);
    }
  }
  return out;
}

// First non-empty value of `key` (parse_qs drops blank values), decoded.
static bool query_value(const std::string &query, const char *key, std::string &out) {
  size_t pos = 0;
  while (pos <= query.size()) {
    size_t amp = query.find('&', pos);
    if (amp == std::string::npos)
      amp = query.size();
    std::string pair = query.substr(pos, amp - pos);
    size_t eq = pair.find('=');
    if (eq != std::string::npos && percent_decode(pair.substr(0, eq)) == key) {
      std::string v = percent_decode(pair.substr(eq + 1));
      if (!v.empty()) {
        out = v;
        return true;
      }
    }
    pos = amp + 1;
  }
  return false;
}

static std::string py_strip(const std::string &s) {
  auto sp = [](char c) { return c == ' ' || (c >= '\t' && c <= '\r') || (c >= 0x1c && c <= 0x1f); };
  size_t b = 0, e = s.size();
  while (b < e && sp(s[b]))
    b++;
  while (e > b && sp(s[e - 1]))
    e--;
  return s.substr(b, e - b);
}

static bool parse_float(const std::string &raw, double &out) {
  std::string s = py_strip(raw);
  if (s.empty() || s.find_first_of("xXpP") != std::string::npos)
    return false;
  char *end = nullptr;
  out = std::strtod(s.c_str(), &end);
  return end == s.c_str() + s.size();
}

static bool const_time_equal(const std::string &a, const std::string &b) {
  // hmac.compare_digest
  size_t n = std::max(a.size(), b.size());
  uint8_t diff = a.size() != b.size();
  for (size_t i = 0; i < n; i++) {
    uint8_t x = i < a.size() ? static_cast<uint8_t>(a[i]) : 0;
    uint8_t y = i < b.size() ? static_cast<uint8_t>(b[i]) : 0;
    diff |= x ^ y;
  }
  return diff == 0;
}

esp_err_t PowerlineProbe::send_json_(httpd_req_t *req, int code, const std::string &body) {
  const char *status;
  switch (code) {
    case 200:
      status = "200 OK";
      break;
    case 400:
      status = "400 Bad Request";
      break;
    case 401:
      status = "401 Unauthorized";
      break;
    case 404:
      status = "404 Not Found";
      break;
    default:
      status = "500 Internal Server Error";
  }
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, body.c_str(), body.size());
}

bool PowerlineProbe::authorized_(httpd_req_t *req) {
  if (this->token_.empty())
    return true;
  std::string header;
  size_t len = httpd_req_get_hdr_value_len(req, "Authorization");
  if (len > 0 && len < 256) {
    header.resize(len + 1);
    if (httpd_req_get_hdr_value_str(req, "Authorization", &header[0], header.size()) == ESP_OK)
      header.resize(len);
    else
      header.clear();
  }
  return const_time_equal(header, "Bearer " + this->token_);
}

esp_err_t PowerlineProbe::http_handler_(httpd_req_t *req) {
  return static_cast<PowerlineProbe *>(req->user_ctx)->handle_(req);
}

esp_err_t PowerlineProbe::handle_(httpd_req_t *req) {
  // Order as in the Python handler: auth first (any path), then routing.
  if (!this->authorized_(req))
    return this->send_json_(req, 401, "{\"error\": \"unauthorized\"}");

  std::string uri(req->uri);
  std::string path = uri, query;
  size_t q = uri.find('?');
  if (q != std::string::npos) {
    path = uri.substr(0, q);
    query = uri.substr(q + 1);
  }

  if (path == "/v1/info")
    return this->send_json_(req, 200, hp::Engine::info_json("ethernet"));

  if (path == "/v1/discover") {
    double timeout = DEFAULT_TIMEOUT;
    std::string v;
    if (query_value(query, "timeout", v) && (!parse_float(v, timeout) || std::isnan(timeout)))
      return this->send_json_(req, 400, "{\"error\": \"bad request\"}");
    timeout = std::min(std::max(timeout, MIN_TIMEOUT), MAX_TIMEOUT);

    std::vector<hp::Device> devices;
    std::vector<hp::Link> links;
    bool failed;
    xSemaphoreTake(this->lock_, portMAX_DELAY);
    this->engine_->discover(timeout, devices, links);
    failed = this->engine_->tx_failed();
    xSemaphoreGive(this->lock_);
    if (failed) {
      ESP_LOGW(TAG, "discover: a frame could not be transmitted (link down?)");
      return this->send_json_(req, 500, "{\"error\": \"probe error\"}");
    }
    ESP_LOGD(TAG, "discover: %u adapters, %u links", static_cast<unsigned>(devices.size()),
             static_cast<unsigned>(links.size()));
    return this->send_json_(req, 200, hp::Engine::discover_json(devices, links));
  }

  if (path == "/v1/states") {
    std::string raw;
    query_value(query, "macs", raw);
    std::vector<std::string> macs;
    size_t pos = 0;
    while (pos <= raw.size()) {
      size_t comma = raw.find(',', pos);
      if (comma == std::string::npos)
        comma = raw.size();
      std::string m = py_strip(raw.substr(pos, comma - pos));
      pos = comma + 1;
      if (m.empty())
        continue;
      for (auto &c : m)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
      hp::Mac parsed;
      if (!hp::mac_from_str(m, parsed))  // bytes.fromhex() ValueError -> 400 in the Python probe
        return this->send_json_(req, 400, "{\"error\": \"bad request\"}");
      macs.push_back(m);
    }
    xSemaphoreTake(this->lock_, portMAX_DELAY);
    auto states = this->engine_->query_states(macs);
    bool failed = this->engine_->tx_failed();
    xSemaphoreGive(this->lock_);
    if (failed) {
      ESP_LOGW(TAG, "states: a frame could not be transmitted (link down?)");
      return this->send_json_(req, 500, "{\"error\": \"probe error\"}");
    }
    return this->send_json_(req, 200, hp::Engine::states_json(states));
  }

  return this->send_json_(req, 404, "{\"error\": \"not found\"}");
}

// ===========================================================================
//  component
// ===========================================================================

void PowerlineProbe::setup() {
  auto *eth = ethernet::global_eth_component;
  if (eth == nullptr || eth->get_esp_netif() == nullptr) {
    ESP_LOGE(TAG, "no Ethernet interface");
    this->mark_failed();
    return;
  }
  if (!this->transport_.init(eth->get_esp_netif())) {
    this->mark_failed();
    return;
  }
  this->lock_ = xSemaphoreCreateMutex();
  this->engine_ = new hp::Engine(&this->transport_);  // NOLINT(cppcoreguidelines-owning-memory)
  if (this->lock_ == nullptr || this->engine_ == nullptr) {
    this->mark_failed();
    return;
  }

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = this->port_;
  // ESPHome's web_server_idf also runs an httpd (port 80, default control
  // port 32768): give this one its own control port.
  config.ctrl_port = static_cast<uint16_t>(ESP_HTTPD_DEF_CTRL_PORT + 1 + (this->port_ % 1000));
  // A request runs a whole discovery in the handler: stack for the engine's
  // callbacks and string building (buffers live on the heap).
  config.stack_size = 8192;
  config.max_open_sockets = 2;
  config.lru_purge_enable = true;
  // Discovery blocks for seconds; keep idle clients from pinning a socket.
  config.recv_wait_timeout = 10;
  config.send_wait_timeout = 10;
  config.uri_match_fn = httpd_uri_match_wildcard;

  if (httpd_start(&this->server_, &config) != ESP_OK) {
    ESP_LOGE(TAG, "cannot start the HTTP server on port %u", this->port_);
    this->mark_failed();
    return;
  }
  httpd_uri_t any_get = {};
  any_get.uri = "/*";
  any_get.method = HTTP_GET;
  any_get.handler = PowerlineProbe::http_handler_;
  any_get.user_ctx = this;
  httpd_register_uri_handler(this->server_, &any_get);
}

void PowerlineProbe::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Powerline probe:\n"
                "  Port: %u\n"
                "  Token: %s\n"
                "  Version: %s",
                this->port_, this->token_.empty() ? "none (open)" : "set", PROBE_VERSION);
  if (this->token_.empty())
    ESP_LOGW(TAG, "No token set: anyone who reaches the port can read the adapter list");
}

}  // namespace powerline_probe
}  // namespace esphome

#endif  // USE_ESP32
