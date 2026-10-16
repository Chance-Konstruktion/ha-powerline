"""ESPHome external component: read-only HomePlug AV probe for ha-powerline.

Talks HomePlug on the board's wired Ethernet port and serves the same JSON
over HTTP as ``probe/powerline_probe.py``, so the integration's "Remote probe"
setup works against it unchanged.
"""

import esphome.codegen as cg
from esphome.components import esp32, socket
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_PORT
from esphome.types import ConfigType

CODEOWNERS = []
DEPENDENCIES = ["ethernet"]

CONF_TOKEN = "token"
CONF_INTERFACE = "interface"
DEFAULT_PORT = 8765

powerline_probe_ns = cg.esphome_ns.namespace("powerline_probe")
PowerlineProbe = powerline_probe_ns.class_("PowerlineProbe", cg.Component)


def _consume_sockets(config: ConfigType) -> ConfigType:
    """esp_http_server: one listening socket, two clients, one control UDP socket."""
    socket.consume_sockets(2, "powerline_probe")(config)
    socket.consume_sockets(1, "powerline_probe", socket.SocketType.TCP_LISTEN)(config)
    socket.consume_sockets(1, "powerline_probe", socket.SocketType.UDP)(config)
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(PowerlineProbe),
            cv.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
            # Shared secret; clients must send "Authorization: Bearer <token>".
            cv.Optional(CONF_TOKEN): cv.string,
            # Accepted for parity with `--interface` of powerline_probe.py and
            # ignored: the probe always uses the board's Ethernet port.
            cv.Optional(CONF_INTERFACE): cv.string,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on_esp32,
    cv.only_with_framework("esp-idf"),
    _consume_sockets,
)


async def to_code(config: ConfigType) -> None:
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_port(config[CONF_PORT]))
    if token := config.get(CONF_TOKEN):
        cg.add(var.set_token(token))

    # esp_http_server is excluded from ESPHome's IDF builds by default.
    esp32.include_builtin_idf_component("esp_http_server")
    esp32.include_builtin_idf_component("esp-tls")

    # Raw layer-2 access through ESP-IDF's L2 TAP (esp_netif): one
    # /dev/net/tap descriptor per EtherType (0x88E1, 0x8912). See
    # powerline_probe.cpp for why this is used instead of an lwIP hook.
    esp32.add_idf_sdkconfig_option("CONFIG_ESP_NETIF_L2_TAP", True)
    # Two descriptors are needed; keep the per-descriptor RX queue modest, a
    # full 1500 byte frame sits in every slot of a queue nobody is reading.
    esp32.add_idf_sdkconfig_option("CONFIG_ESP_NETIF_L2_TAP_MAX_FDS", 2)
    esp32.add_idf_sdkconfig_option("CONFIG_ESP_NETIF_L2_TAP_RX_QUEUE_SIZE", 16)
    # The probe waits for frames with select() on the TAP descriptors.
    esp32.require_vfs_select()
