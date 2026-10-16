#pragma once

#include "esphome/core/defines.h"

#ifdef USE_ESP32

#include <esp_http_server.h>
#include <esp_netif.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <string>

#include "esphome/core/component.h"
#include "homeplug.h"

namespace esphome {
namespace powerline_probe {

// Raw layer-2 access for the engine, on ESP-IDF's L2 TAP (esp_netif).
//
// One /dev/net/tap descriptor per EtherType. The Ethernet driver's input path
// hands matching frames to the descriptor's queue instead of lwIP, and
// write() transmits through esp_eth_transmit() (CONFIG_ETH_TRANSMIT_MUTEX is
// selected by L2 TAP, so it is safe next to lwIP's own transmits).
class L2tapTransport : public hp::Transport {
 public:
  bool init(esp_netif_t *netif);

  void open(uint16_t ethertype) override;
  void close() override;
  bool send(const uint8_t *frame, size_t len) override;
  size_t recv(uint16_t ethertype, uint8_t *buf, size_t cap, uint32_t timeout_ms) override;
  uint32_t now_ms() override;
  hp::Mac own_mac() override;

 protected:
  int &fd_for_(uint16_t ethertype);

  esp_netif_t *netif_{nullptr};
  int fd_hpav_{-1};
  int fd_mx_{-1};
};

class PowerlineProbe : public Component {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void set_port(uint16_t port) { this->port_ = port; }
  void set_token(const std::string &token) { this->token_ = token; }

 protected:
  static esp_err_t http_handler_(httpd_req_t *req);
  esp_err_t handle_(httpd_req_t *req);
  bool authorized_(httpd_req_t *req);
  esp_err_t send_json_(httpd_req_t *req, int code, const std::string &body);

  uint16_t port_{8765};
  std::string token_;
  httpd_handle_t server_{nullptr};
  L2tapTransport transport_;
  hp::Engine *engine_{nullptr};
  SemaphoreHandle_t lock_{nullptr};
};

}  // namespace powerline_probe
}  // namespace esphome

#endif  // USE_ESP32
