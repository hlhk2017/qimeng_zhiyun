#include "fastcon_tx.h"

#include <algorithm>
#include <utility>

#include "esphome/core/log.h"
#include "fastcon_tx_util.h"

namespace esphome {
namespace fastcon_tx {

static const char *const TAG = "fastcon_tx";

// ---------------------------------------------------------------------------
// Hex parsing
// ---------------------------------------------------------------------------

bool FastconTx::parse_hex(const std::string &hex, std::vector<uint8_t> *out) {
  return util::parse_hex(hex, out);
}

bool FastconTx::send_hex(const std::string &hex) {
  std::vector<uint8_t> payload;
  if (!util::parse_hex(hex, &payload)) {
    ESP_LOGW(TAG, "Ignoring payload '%s': not a valid hex string (odd length or a non-hex character)",
             hex.c_str());
    return false;
  }
  if (payload.empty()) {
    ESP_LOGW(TAG, "Ignoring empty payload");
    return false;
  }
  this->send_payload(payload);
  return true;
}

// ---------------------------------------------------------------------------
// Frame building / queueing
// ---------------------------------------------------------------------------

void FastconTx::enqueue_(std::vector<uint8_t> adv_data) {
  std::lock_guard<std::mutex> lock(this->queue_mutex_);
  // Drop a duplicate that is still waiting, so a retried command (HA often sends
  // the same state twice) does not pile up behind itself.
  for (auto &frame : this->queue_) {
    if (frame.adv_data == adv_data) {
      frame.sends_left = this->repeat_count_;
      return;
    }
  }
  if (this->queue_.size() >= this->max_queue_size_) {
    ESP_LOGW(TAG, "Broadcast queue full (%u), dropping frame", (unsigned) this->queue_.size());
    return;
  }
  this->queue_.push_back(Frame{std::move(adv_data), this->repeat_count_});
}

void FastconTx::send_payload(const std::vector<uint8_t> &payload) {
  if (payload.empty()) {
    ESP_LOGW(TAG, "Ignoring empty payload");
    return;
  }
  if (payload.size() > util::MAX_ADV_PAYLOAD) {
    ESP_LOGW(TAG, "Payload is %u bytes, but a BLE advertisement only fits %u; truncating",
             (unsigned) payload.size(), (unsigned) util::MAX_ADV_PAYLOAD);
  }

  std::vector<uint8_t> adv_data = util::build_adv_data(payload, this->adv_flags_, nullptr);
  ESP_LOGI(TAG, "Queued broadcast (%u byte payload) adv data: %s", (unsigned) payload.size(),
           util::to_hex(adv_data).c_str());
  this->enqueue_(std::move(adv_data));
}

size_t FastconTx::get_queue_size() {
  std::lock_guard<std::mutex> lock(this->queue_mutex_);
  return this->queue_.size();
}

// ---------------------------------------------------------------------------
// Component lifecycle
// ---------------------------------------------------------------------------

void FastconTx::setup() { ESP_LOGCONFIG(TAG, "FastCon BLE broadcast transmitter ready"); }

void FastconTx::dump_config() {
  ESP_LOGCONFIG(TAG, "fastcon_tx (dumb BLE broadcaster, no mesh key on the device):");
  ESP_LOGCONFIG(TAG, "  Advertisement duration: %ums, gap: %ums", this->adv_duration_, this->adv_gap_);
  ESP_LOGCONFIG(TAG, "  Broadcasts per frame: %u", (unsigned) this->repeat_count_);
  ESP_LOGCONFIG(TAG, "  Advertising interval: %u-%u (0.625ms units)", (unsigned) this->adv_interval_min_,
                (unsigned) this->adv_interval_max_);
  ESP_LOGCONFIG(TAG, "  Advertising PDU: %s, flags 0x%02X",
                this->adv_connectable_ ? "ADV_IND (connectable)" : "ADV_NONCONN_IND",
                this->adv_flags_);
  if (this->has_tx_power_) {
    ESP_LOGCONFIG(TAG, "  Advertising TX power level: %d", (int) this->tx_power_);
  } else {
    ESP_LOGCONFIG(TAG, "  Advertising TX power: Bluetooth stack default (level %d)",
                  (int) esp_ble_tx_power_get(ESP_BLE_PWR_TYPE_ADV));
  }
}

void FastconTx::loop() {
  const uint32_t now = millis();

  switch (this->adv_state_) {
    case AdvertiseState::IDLE: {
      Frame frame;
      {
        std::lock_guard<std::mutex> lock(this->queue_mutex_);
        if (this->queue_.empty())
          return;
        frame = std::move(this->queue_.front());
        this->queue_.pop_front();
      }

      // Re-queue for the remaining repeats, at the back, so several frames
      // waiting at once still each get airtime.
      if (frame.sends_left > 1) {
        std::lock_guard<std::mutex> lock(this->queue_mutex_);
        if (this->queue_.size() < this->max_queue_size_) {
          Frame again = frame;
          again.sends_left = frame.sends_left - 1;
          this->queue_.push_back(std::move(again));
        }
      }

      esp_ble_adv_params_t adv_params = {
          .adv_int_min = this->adv_interval_min_,
          .adv_int_max = this->adv_interval_max_,
          // The brMesh app sends a connectable undirected PDU (Android ADV_IND); the
          // bulbs ignore the same data in a non-connectable PDU, so default to ADV_IND.
          .adv_type = this->adv_connectable_ ? ADV_TYPE_IND : ADV_TYPE_NONCONN_IND,
          .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
          .peer_addr = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00},
          .peer_addr_type = BLE_ADDR_TYPE_PUBLIC,
          .channel_map = ADV_CHNL_ALL,
          .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
      };

      // Applied on first use rather than in setup(), so the Bluetooth stack is certainly up
      if (this->has_tx_power_ && !this->tx_power_applied_) {
        esp_err_t power_err = esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, this->tx_power_);
        if (power_err != ESP_OK) {
          ESP_LOGW(TAG, "Setting advertising TX power failed: %s", esp_err_to_name(power_err));
        }
        this->tx_power_applied_ = true;
      }

      esp_err_t err = esp_ble_gap_config_adv_data_raw(frame.adv_data.data(), frame.adv_data.size());
      if (err != ESP_OK) {
        ESP_LOGW(TAG, "Error setting raw advertisement data (err=%d): %s", err, esp_err_to_name(err));
        return;
      }
      err = esp_ble_gap_start_advertising(&adv_params);
      if (err != ESP_OK) {
        ESP_LOGW(TAG, "Error starting advertisement (err=%d): %s", err, esp_err_to_name(err));
        return;
      }

      this->adv_state_ = AdvertiseState::ADVERTISING;
      this->state_start_time_ = now;
      break;
    }

    case AdvertiseState::ADVERTISING: {
      if (now - this->state_start_time_ >= this->adv_duration_) {
        esp_err_t err = esp_ble_gap_stop_advertising();
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
          // INVALID_STATE just means advertising already ended (e.g. a peer connected)
          ESP_LOGW(TAG, "Error stopping advertisement (err=%d): %s", err, esp_err_to_name(err));
        }
        this->adv_state_ = AdvertiseState::GAP;
        this->state_start_time_ = now;
      }
      break;
    }

    case AdvertiseState::GAP: {
      if (now - this->state_start_time_ >= this->adv_gap_) {
        this->adv_state_ = AdvertiseState::IDLE;
      }
      break;
    }
  }
}

}  // namespace fastcon_tx
}  // namespace esphome
