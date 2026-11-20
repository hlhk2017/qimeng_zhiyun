"""Dumb Broadlink FastCon / brMesh BLE broadcast transmitter.

This component only broadcasts a finished BLE manufacturer-specific payload
(company id 0xFFF0) for a short while. It deliberately knows nothing about the
FastCon protocol (mesh key, whitening, CRC, ...): Home Assistant builds the
payload, so the firmware never contains a mesh key.

See reference/ for the MIT-licensed protocol implementation this was derived from:
https://github.com/dennispg/esphome-fastcon
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import esp32_ble
from esphome.const import CONF_ID

CODEOWNERS = ["@qimeng"]
DEPENDENCIES = ["esp32_ble"]

CONF_PAYLOAD = "payload"
CONF_ADV_DURATION = "adv_duration"
CONF_ADV_GAP = "adv_gap"
CONF_ADV_INTERVAL_MIN = "adv_interval_min"
CONF_ADV_INTERVAL_MAX = "adv_interval_max"
CONF_REPEAT_COUNT = "repeat_count"
CONF_TX_POWER = "tx_power"
CONF_ADV_CONNECTABLE = "adv_connectable"
CONF_ADV_FLAGS = "adv_flags"

# Verified defaults (this is the configuration the working firmware was built with).
# The brMesh app uses a connectable ADV_IND with flags 0x02, but that turned out not to
# matter to the bulbs (the real fix was padding the command data to 12 bytes on the HA
# side). adv_connectable / adv_flags stay configurable for experiments.
DEFAULT_ADV_DURATION = 50
DEFAULT_ADV_GAP = 10
DEFAULT_ADV_INTERVAL_MIN = 0x20
DEFAULT_ADV_INTERVAL_MAX = 0x40
DEFAULT_REPEAT_COUNT = 2
DEFAULT_ADV_CONNECTABLE = False
DEFAULT_ADV_FLAGS = 0x1A

esp_power_level_t = cg.global_ns.enum("esp_power_level_t")
# Advertising TX power in dBm
TX_POWER_LEVELS = {
    -12: esp_power_level_t.ESP_PWR_LVL_N12,
    -9: esp_power_level_t.ESP_PWR_LVL_N9,
    -6: esp_power_level_t.ESP_PWR_LVL_N6,
    -3: esp_power_level_t.ESP_PWR_LVL_N3,
    0: esp_power_level_t.ESP_PWR_LVL_N0,
    3: esp_power_level_t.ESP_PWR_LVL_P3,
    6: esp_power_level_t.ESP_PWR_LVL_P6,
    9: esp_power_level_t.ESP_PWR_LVL_P9,
}

fastcon_tx_ns = cg.esphome_ns.namespace("fastcon_tx")
FastconTx = fastcon_tx_ns.class_("FastconTx", cg.Component)
SendPayloadAction = fastcon_tx_ns.class_(
    "SendPayloadAction", automation.Action, cg.Parented.template(FastconTx)
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(FastconTx),
        # Pulls the Bluetooth controller component into the config even when the YAML
        # has no explicit esp32_ble:/esp32_ble_tracker: block (e.g. a device that only
        # has bluetooth_proxy), so "requires component esp32_ble" cannot happen.
        cv.GenerateID(esp32_ble.CONF_BLE_ID): cv.use_id(esp32_ble.ESP32BLE),
        # How long each broadcast is kept on air, and the pause afterwards
        cv.Optional(CONF_ADV_DURATION, default=DEFAULT_ADV_DURATION): cv.uint16_t,
        cv.Optional(CONF_ADV_GAP, default=DEFAULT_ADV_GAP): cv.uint16_t,
        cv.Optional(
            CONF_ADV_INTERVAL_MIN, default=DEFAULT_ADV_INTERVAL_MIN
        ): cv.uint16_t,
        cv.Optional(
            CONF_ADV_INTERVAL_MAX, default=DEFAULT_ADV_INTERVAL_MAX
        ): cv.uint16_t,
        # Connectable (ADV_IND, like the brMesh app) or non-connectable (ADV_NONCONN_IND)
        cv.Optional(CONF_ADV_CONNECTABLE, default=DEFAULT_ADV_CONNECTABLE): cv.boolean,
        # Advertising flags AD byte: 0x02 = LE General Discoverable (what the app sends)
        cv.Optional(CONF_ADV_FLAGS, default=DEFAULT_ADV_FLAGS): cv.hex_uint8_t,
        # How many times each frame is broadcast (the bulb may miss one)
        cv.Optional(CONF_REPEAT_COUNT, default=DEFAULT_REPEAT_COUNT): cv.int_range(
            min=1, max=5
        ),
        # Advertising TX power in dBm; left at the Bluetooth stack default unless set
        cv.Optional(CONF_TX_POWER): cv.enum(TX_POWER_LEVELS, int=True),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    if config[CONF_ADV_INTERVAL_MAX] < config[CONF_ADV_INTERVAL_MIN]:
        raise cv.Invalid(
            f"adv_interval_max ({config[CONF_ADV_INTERVAL_MAX]}) must be >= "
            f"adv_interval_min ({config[CONF_ADV_INTERVAL_MIN]})"
        )

    cg.add(var.set_adv_duration_ms(config[CONF_ADV_DURATION]))
    cg.add(var.set_adv_gap_ms(config[CONF_ADV_GAP]))
    cg.add(
        var.set_adv_interval(
            config[CONF_ADV_INTERVAL_MIN], config[CONF_ADV_INTERVAL_MAX]
        )
    )
    cg.add(var.set_repeat_count(config[CONF_REPEAT_COUNT]))
    cg.add(var.set_adv_connectable(config[CONF_ADV_CONNECTABLE]))
    cg.add(var.set_adv_flags(config[CONF_ADV_FLAGS]))

    if CONF_TX_POWER in config:
        cg.add(var.set_tx_power(config[CONF_TX_POWER]))


# fastcon_tx.send: broadcast one finished payload, given as a hex string
@automation.register_action(
    "fastcon_tx.send",
    SendPayloadAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(FastconTx),
            cv.Required(CONF_PAYLOAD): cv.templatable(cv.string),
        }
    ),
    synchronous=True,
)
async def send_payload_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    payload = await cg.templatable(
        config[CONF_PAYLOAD], args, cg.std_string
    )
    cg.add(var.set_payload(payload))
    return var