#pragma once

// fastcon_tx -- a "dumb" Broadlink FastCon / brMesh BLE broadcast transmitter.
//
// This component knows NOTHING about the FastCon protocol. It only takes a
// finished BLE manufacturer-specific payload (the 0xFFF0 blob that the brMesh
// phone app puts into a BLE advertisement), wraps it in the usual advertising
// AD structures and broadcasts it for a short while.
//
// All protocol work (mesh key, header XOR, whitening, CRC, reversed address)
// is done by the Home Assistant side, so this firmware holds no keys.
//
// Original protocol reference implementation: https://github.com/dennispg/esphome-fastcon
// (MIT). See reference/ and LICENSE in this directory.

#include <deque>
#include <mutex>
#include <string>
#include <vector>

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#include <esp_bt.h>
#include <esp_gap_ble_api.h>

namespace esphome {
namespace fastcon_tx {

// The advertising data layout (flags + manufacturer specific data with company
// id 0xFFF0, payload limited to 24 bytes) lives in fastcon_tx_util.h, which has no
// ESPHome/ESP-IDF dependencies so it can be unit tested on a PC.

class FastconTx : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  // Needs the Bluetooth controller to be up (esp32_ble sets it up at BLUETOOTH priority)
  float get_setup_priority() const override { return setup_priority::AFTER_BLUETOOTH; }

  // Broadcast a finished manufacturer payload (the bytes that go after the company id).
  void send_payload(const std::vector<uint8_t> &payload);
  // Same, from a hex string. Whitespace, ':' and '-' separators are ignored.
  // Returns false (and logs) when the string is not valid hex.
  bool send_hex(const std::string &hex);

  // Static helper so it can be unit tested / reused by the template text entity.
  static bool parse_hex(const std::string &hex, std::vector<uint8_t> *out);

  void set_adv_duration_ms(uint16_t val) { adv_duration_ = val; }
  // Connectable (ADV_IND) like the phone app, or non-connectable (ADV_NONCONN_IND)
  void set_adv_connectable(bool val) { adv_connectable_ = val; }
  // Advertising flags byte (0x02 = LE General Discoverable, as sent by the brMesh app)
  void set_adv_flags(uint8_t val) { adv_flags_ = val; }
  void set_adv_gap_ms(uint16_t val) { adv_gap_ = val; }
  void set_repeat_count(uint8_t val) { repeat_count_ = val < 1 ? 1 : val; }
  void set_adv_interval(uint16_t min_us, uint16_t max_us) {
    adv_interval_min_ = min_us;
    adv_interval_max_ = max_us < min_us ? min_us : max_us;
  }
  // Advertising TX power, applied on first use. Left at the Bluetooth stack default unless set.
  void set_tx_power(esp_power_level_t level) {
    tx_power_ = level;
    has_tx_power_ = true;
  }

  size_t get_queue_size();

 protected:
  struct Frame {
    std::vector<uint8_t> adv_data;  // complete raw advertising data, ready for the controller
    uint8_t sends_left;
  };

  void enqueue_(std::vector<uint8_t> adv_data);

  std::deque<Frame> queue_;
  mutable std::mutex queue_mutex_;

  enum class AdvertiseState : uint8_t { IDLE, ADVERTISING, GAP };
  AdvertiseState adv_state_{AdvertiseState::IDLE};
  uint32_t state_start_time_{0};

  uint16_t adv_interval_min_{0x20};
  uint16_t adv_interval_max_{0x40};
  uint16_t adv_duration_{50};
  uint16_t adv_gap_{10};
  uint8_t repeat_count_{2};
  // Verified defaults: non-connectable ADV_NONCONN_IND, flags 0x1A (both configurable).
  // The brMesh app uses connectable ADV_IND + flags 0x02, but the bulbs ignore that
  // difference; what actually matters is the 12-byte data padding on the HA side.
  bool adv_connectable_{false};
  uint8_t adv_flags_{0x1A};
  size_t max_queue_size_{64};

  esp_power_level_t tx_power_{ESP_PWR_LVL_P9};
  bool has_tx_power_{false};
  bool tx_power_applied_{false};
};

// fastcon_tx.send -- one broadcast of a finished payload, given as a hex string.
template<typename... Ts> class SendPayloadAction : public Action<Ts...>, public Parented<FastconTx> {
 public:
  TEMPLATABLE_VALUE(std::string, payload)

  void play(const Ts &...x) override {
    auto payload = this->payload_.value(x...);
    this->parent_->send_hex(payload);
  }
};

}  // namespace fastcon_tx
}  // namespace esphome