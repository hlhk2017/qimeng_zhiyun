#include "fastcon_tx_util.h"

#include <algorithm>

namespace esphome {
namespace fastcon_tx {
namespace util {

bool parse_hex(const std::string &hex, std::vector<uint8_t> *out) {
  out->clear();
  int hi = -1;
  for (char c : hex) {
    if (c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ':' || c == ',' || c == '-') {
      continue;  // separators are fine, so log output can be pasted back in
    }
    int v;
    if (c >= '0' && c <= '9') {
      v = c - '0';
    } else if (c >= 'a' && c <= 'f') {
      v = c - 'a' + 10;
    } else if (c >= 'A' && c <= 'F') {
      v = c - 'A' + 10;
    } else {
      return false;
    }
    if (hi < 0) {
      hi = v;
    } else {
      out->push_back(static_cast<uint8_t>((hi << 4) | v));
      hi = -1;
    }
  }
  // An odd number of hex digits leaves a dangling high nibble: invalid.
  return hi < 0;
}

std::vector<uint8_t> build_adv_data(const std::vector<uint8_t> &payload, uint8_t flags, bool *truncated) {
  std::vector<uint8_t> data = payload;
  if (truncated != nullptr) {
    *truncated = data.size() > MAX_ADV_PAYLOAD;
  }
  if (data.size() > MAX_ADV_PAYLOAD) {
    data.resize(MAX_ADV_PAYLOAD);
  }

  std::vector<uint8_t> adv_data;
  adv_data.reserve(data.size() + 7);

  // Flags AD structure: length 2, type 0x01, value <flags>
  adv_data.push_back(2);
  adv_data.push_back(0x01);  // ESP_BLE_AD_TYPE_FLAG
  adv_data.push_back(flags);

  // Manufacturer specific AD structure:
  // length = 1 (type) + 2 (company id) + payload, type 0xFF, company id little endian
  adv_data.push_back(static_cast<uint8_t>(data.size() + 3));
  adv_data.push_back(0xFF);  // ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE
  adv_data.push_back(static_cast<uint8_t>(COMPANY_ID & 0xFF));
  adv_data.push_back(static_cast<uint8_t>((COMPANY_ID >> 8) & 0xFF));
  adv_data.insert(adv_data.end(), data.begin(), data.end());

  return adv_data;
}

std::string to_hex(const std::vector<uint8_t> &data) {
  static const char *const HEX = "0123456789ABCDEF";
  std::string out;
  out.reserve(data.size() * 2);
  for (uint8_t b : data) {
    out.push_back(HEX[b >> 4]);
    out.push_back(HEX[b & 0x0F]);
  }
  return out;
}

}  // namespace util
}  // namespace fastcon_tx
}  // namespace esphome