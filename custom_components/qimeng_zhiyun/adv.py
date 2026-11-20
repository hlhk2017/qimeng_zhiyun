"""FastCon 广播帧解析（灯泡 -> 手机/HA 方向）。

收到的 BLE 厂商数据（company id 0xFFF0）有两种形态：

1. RF 风格（ESPHome fastcon 组件发送、部分固件心跳）：
   payload = white(buf[15:])
   buf = [0] * 15 + [0x8E, 0xF0, 0xAA] + reversed_bits(addr) + body + crc16(le)
   （0x8E/0xF0/0xAA 是 0x71/0x0F/0x55 逐位反转后的值）

2. BLE 风格（手机 App 发送）：payload = [0xA5, 0x5A] + body（可能仍然白化）

body = header(4) + data，header 用固定 key 异或、data 用 mesh_key 逐字节异或。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from . import fastcon
from .const import COMPANY_ID

_LOGGER = logging.getLogger(__name__)

RF_MAGIC = bytes([0x8E, 0xF0, 0xAA])
BLE_MAGIC = bytes([0xA5, 0x5A])


@dataclass
class BulbAdvertisement:
    """一条解析后的灯泡广播。"""

    address: str
    payload: bytes
    rssi: int | None = None
    name: str | None = None
    light_id: int | None = None
    cmd_type: int | None = None
    sequence: int | None = None
    data: bytes = b""
    state: dict[str, int] = field(default_factory=dict)
    kind: str = "unknown"


def _unwhiten(payload: bytes) -> bytes:
    """把 payload 白化还原成完整 buf[15:] 的形式。"""
    total = len(payload) + 15
    stream = fastcon.Whitening().keystream(total)
    return bytes(a ^ b for a, b in zip(payload, stream[15:]))


def _decode_body(body: bytes, mesh_key: bytes | None) -> tuple[bytes, bytes] | None:
    """解密 body → (header, data)。header 4 字节固定 key 异或。"""
    if len(body) < 4:
        return None
    header = bytes(body[i] ^ fastcon.DEFAULT_ENCRYPT_KEY[i & 3] for i in range(4))
    data = bytes(body[4:])
    if mesh_key:
        data = bytes(byte ^ mesh_key[i & 3] for i, byte in enumerate(data))
    # 校验和：明文 header[0..2] + 明文 data 的字节和等于 header[3]
    checksum = (header[0] + header[1] + header[2] + sum(data)) & 0xFF
    if checksum != header[3]:
        return None
    return header, data


def parse_advertisement(
    address: str,
    payload: bytes,
    *,
    mesh_key: bytes | None = None,
    rssi: int | None = None,
    name: str | None = None,
) -> BulbAdvertisement | None:
    """解析一条厂商数据，识别是不是 FastCon 灯，并尽量读出状态。"""
    if not payload:
        return None

    raw = payload
    body: bytes | None = None
    addr: bytes | None = None

    # 1) RF 风格
    decoded = _unwhiten(payload)
    if decoded[:3] == RF_MAGIC:
        addr = bytes(fastcon.reverse_8(b) for b in reversed(decoded[3:6]))
        body = decoded[6:-2]
        expect = decoded[-2] | (decoded[-1] << 8)
        if fastcon.crc16(addr, body) != expect:
            _LOGGER.debug("FastCon CRC 不匹配（%s），忽略", address)
            return None

    # 2) BLE 风格（未白化或简单前缀）
    if body is None and payload[:2] == BLE_MAGIC:
        body = payload[2:]

    if body is None:
        # 不是 FastCon 帧（或者是我们自己发出的广播被扫回），一律忽略
        status = BulbAdvertisement(address=address, payload=raw, rssi=rssi, name=name)
        status.kind = "unknown"
        return status

    decoded_body = _decode_body(body, mesh_key)
    if decoded_body is None:
        status = BulbAdvertisement(address=address, payload=raw, rssi=rssi, name=name)
        status.kind = "unparsed"
        return status

    header, data = decoded_body
    cmd_type = (header[0] >> 4) & 0x07
    forward = bool(header[0] & 0x80)
    light_id_high = header[0] & 0x0F

    status = BulbAdvertisement(address=address, payload=raw, rssi=rssi, name=name)
    status.cmd_type = cmd_type
    status.sequence = header[1]
    status.data = data
    status.kind = "control" if (cmd_type == 5 and forward) else ("rf" if addr else "ble")

    # 单灯控制/状态帧：data[0] 低 4 位 = 2，data[1] = light_id
    if data and (data[0] & 0x0F) == 2 and len(data) >= 2:
        status.light_id = data[1]

    # 组帧：低 4 位 = 3，后面是 0x2A 0xA8 group
    elif data and (data[0] & 0x0F) == 3 and len(data) >= 4 and data[1] == 0x2A:
        status.kind = "group"

    # 心跳/状态帧：把剩余字节原样放出来，具体字段待实测确认
    if status.kind not in ("rf", "ble", "control", "group"):
        status.kind = "status"

    if data and status.light_id is None and (data[0] & 0x80):
        status.light_id = data[0] & 0x7F

    return status


def build_rf_payload_from_ble_payload(payload: bytes) -> BulbAdvertisement | None:
    """兼容旧接口：只看 payload。"""
    return parse_advertisement("", payload)