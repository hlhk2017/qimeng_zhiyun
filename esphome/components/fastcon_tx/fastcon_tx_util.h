#pragma once

// Pure, dependency-free helpers used by the fastcon_tx component.
//
// Kept separate from fastcon_tx.cpp so the exact same code can be compiled and
// tested on a PC (see /tmp/qm/fw/test_protocol.py) without ESPHome, ESP-IDF or
// a board: it only needs <string> and <vector>.
//
// The payload here is the finished BLE manufacturer-specific data blob the
// Broadlink FastCon / brMesh phone app broadcasts, i.e. the bytes that follow
// the company id 0xFFF0. Building that blob (mesh key, header XOR, whitening,
// CRC, reversed address) is Home Assistant's job, not the firmware's.

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace esphome {
namespace fastcon_tx {
namespace util {

// Broadlink FastCon / brMesh company id, little endian on the air: F0 FF
static const uint16_t COMPANY_ID = 0xFFF0;

// A legacy BLE advertisement holds at most 31 bytes:
//   3 (flags AD) + 1 (AD length) + 3 (type + company id) + payload <= 31
// => payload <= 24 bytes.
static const size_t MAX_ADV_PAYLOAD = 24;

// Default advertising flags value (0x1A = LE General Discoverable + BR/EDR not
// supported). This is the value that the verified firmware ships with.
// NOTE: the brMesh phone app actually sends flags 0x02 inside a *connectable*
// ADV_IND; it turned out the bulbs do not care about the PDU shape or the flags
// at all (the missing piece was padding the command data to 12 bytes), so the
// default stays at the tested value. Both are configurable via adv_flags /
// adv_connectable in the YAML for experiments.
static const uint8_t ADV_FLAGS = 0x1A;

// Parse a hex string. Whitespace and ':', ',', '-' separators are ignored.
// Returns false on a non-hex character or an odd number of digits.
bool parse_hex(const std::string &hex, std::vector<uint8_t> *out);

// Build the complete raw advertising data for a manufacturer payload:
//   02 01 <flags>                     flags AD (length 2, type 0x01)
//   len  FF  F0 FF  <payload...>      manufacturer specific data
// Payloads longer than MAX_ADV_PAYLOAD are truncated; *truncated is set to true
// when that happened (optional, may be nullptr).
std::vector<uint8_t> build_adv_data(const std::vector<uint8_t> &payload, uint8_t flags,
                                    bool *truncated);

// Upper case hex, e.g. "02011A..." (logging / debugging only)
std::string to_hex(const std::vector<uint8_t> &data);

}  // namespace util
}  // namespace fastcon_tx
}  // namespace esphome