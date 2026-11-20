"""Broadlink FastCon / brMesh BLE 灯控协议（纯 Python 实现）。

协议参考：https://github.com/dennispg/esphome-fastcon (MIT)
  - 帧由 HA 侧构造完整"厂商数据 payload"，固件只负责广播。
  - 发送：BLE 广播，厂商 ID 0xFFF0，payload 即本模块输出。

结构：
  body = header(4) + data
  header[0] = (i2 & 0x0F) | ((cmd & 0x07) << 4) | (0x80 if forward else 0)   # i2 = light_id // 256
  header[1] = sequence
  header[2] = mesh_key[3]
  header[3] = sum(body[i] for i != 3) & 0xFF
  header[0..3] ^= DEFAULT_ENCRYPT_KEY
  data[i] ^= mesh_key[i & 3]

  prepare_payload(addr=[0xC1,0xC2,0xC3], body):
    buf = 15 字节 0 + 0x71,0x0F,0x55 + reverse(addr) + body + crc16(le)
    对 buf[0x0F:0x0F+len(addr)+3] 逐字节位反转
    whitening_init(0x25) 后整段白化
    返回 buf[0x0F:]
"""

from __future__ import annotations

import hashlib

DEFAULT_ENCRYPT_KEY = bytes([0x5E, 0x36, 0x7B, 0xC4])
FASTCON_ADDRESS = bytes([0xC1, 0xC2, 0xC3])
WHITENING_INIT = 0x25
COMPANY_ID = 0xFFF0

CMD_CONTROL = 5
DEFAULT_SEQUENCE = 0x2A


def reverse_8(value: int) -> int:
    out = 0
    for i in range(8):
        out |= ((value >> i) & 1) << (7 - i)
    return out & 0xFF


def reverse_16(value: int) -> int:
    out = 0
    for i in range(16):
        out |= ((value >> i) & 1) << (15 - i)
    return out & 0xFFFF


def _crc_step(crc: int) -> int:
    tmp = (crc << 1) & 0xFFFF
    if crc & 0x8000:
        tmp ^= 0x1021
    crc = (tmp << 1) & 0xFFFF
    if tmp & 0x8000:
        crc ^= 0x1021
    return crc & 0xFFFF


def crc16(addr: bytes, data: bytes) -> int:
    crc = 0xFFFF
    for byte in reversed(addr):
        crc ^= (byte << 8) & 0xFFFF
        for _ in range(4):
            crc = _crc_step(crc)
    for byte in data:
        crc ^= (reverse_8(byte) << 8) & 0xFFFF
        for _ in range(4):
            crc = _crc_step(crc)
    return (~reverse_16(crc)) & 0xFFFF


class Whitening:
    """streaming whitening，端口自 C++ 实现。"""

    def __init__(self, seed: int = WHITENING_INIT) -> None:
        bits = [(seed >> 5) & 1, (seed >> 4) & 1, (seed >> 3) & 1, (seed >> 2) & 1]
        self.f0 = 1
        self.f4 = bits[0]
        self.f8 = bits[1]
        self.fc = bits[2]
        self.f10 = bits[3]
        self.f14 = (seed >> 1) & 1
        self.f18 = seed & 1

    def keystream(self, count: int) -> bytes:
        """返回 count 字节的白化掩码（encode/decode 都是与它异或）。"""
        out = bytearray()
        for _ in range(count):
            var_c = self.fc
            var14 = self.f14
            var18 = self.f18
            var10 = self.f10
            var8 = var14 ^ self.f8
            var4 = var10 ^ self.f4
            var = var18 ^ var_c
            var0 = var ^ self.f0
            mask = (
                ((var8 ^ var18) << 7)
                + (var0 << 6)
                + (var4 << 5)
                + (var8 << 4)
                + (var << 3)
                + (var10 << 2)
                + (var14 << 1)
                + var18
            )
            out.append(mask & 0xFF)
            self.f8 = var4
            self.fc = var8
            self.f10 = var8 ^ var_c
            self.f14 = var0 ^ var10
            self.f18 = var4 ^ var14
            self.f0 = var8 ^ var18
            self.f4 = var0
        return bytes(out)

    def encode(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            var_c = self.fc
            var14 = self.f14
            var18 = self.f18
            var10 = self.f10
            var8 = var14 ^ self.f8
            var4 = var10 ^ self.f4
            var = var18 ^ var_c
            var0 = var ^ self.f0
            c = byte
            value = (
                ((c & 0x80) ^ ((var8 ^ var18) << 7))
                + ((c & 0x40) ^ (var0 << 6))
                + ((c & 0x20) ^ (var4 << 5))
                + ((c & 0x10) ^ (var8 << 4))
                + ((c & 0x08) ^ (var << 3))
                + ((c & 0x04) ^ (var10 << 2))
                + ((c & 0x02) ^ (var14 << 1))
                + ((c & 0x01) ^ var18)
            )
            out.append(value & 0xFF)
            self.f8 = var4
            self.fc = var8
            self.f10 = var8 ^ var_c
            self.f14 = var0 ^ var10
            self.f18 = var4 ^ var14
            self.f0 = var8 ^ var18
            self.f4 = var0
        return bytes(out)


def get_rf_payload(addr: bytes, data: bytes) -> bytes:
    data_offset = 0x12
    inverse_offset = 0x0F
    result_size = data_offset + len(addr) + len(data)
    buf = bytearray(result_size + 2)
    buf[0x0F] = 0x71
    buf[0x10] = 0x0F
    buf[0x11] = 0x55
    for i, byte in enumerate(addr):
        buf[data_offset + len(addr) - i - 1] = byte
    buf[data_offset + len(addr):result_size] = data
    for i in range(inverse_offset, inverse_offset + len(addr) + 3):
        buf[i] = reverse_8(buf[i])
    checksum = crc16(addr, data)
    buf[result_size] = checksum & 0xFF
    buf[result_size + 1] = (checksum >> 8) & 0xFF
    return bytes(buf)


def prepare_payload(addr: bytes, data: bytes) -> bytes:
    """返回可放进 BLE 厂商数据（company id 0xFFF0）的 payload。"""
    buf = get_rf_payload(addr, data)
    whitened = Whitening().encode(buf)
    return whitened[0x0F:]


def generate_command(
    cmd_type: int,
    light_id: int,
    mesh_key: bytes,
    data: bytes,
    *,
    forward: bool = True,
    sequence: int = DEFAULT_SEQUENCE,
    addr_nibble: bool = True,
) -> bytes:
    # header[0] 的低 4 位：启明智云 App 放的是 light_id 的低 4 位（实测 key:559d9882、
    # 灯 1 开灯帧头 d1cf82c5 → 低 nibble=1）；esphome-fastcon 放的是 light_id//256。
    # 默认按 App 行为，addr_nibble=False 可切回 esphome 行为。
    i2 = (light_id & 0x0F) if addr_nibble else ((light_id // 256) & 0x0F)
    body = bytearray(len(data) + 4)
    body[0] = (i2 & 0x0F) | ((cmd_type & 0x07) << 4) | (0x80 if forward else 0)
    body[1] = sequence & 0xFF
    body[2] = mesh_key[3]
    body[4:] = data
    body[3] = sum(b for i, b in enumerate(body) if i != 3) & 0xFF
    for i in range(4):
        body[i] ^= DEFAULT_ENCRYPT_KEY[i & 3]
    for i in range(len(data)):
        body[4 + i] ^= mesh_key[i & 3]
    return prepare_payload(FASTCON_ADDRESS, bytes(body))


# ---------------------------------------------------------------- light data

CH_COLD_WARM = "cold_warm"


def light_data(
    *,
    is_on: bool,
    brightness: int = 255,
    rgb: tuple[int, int, int] | None = None,
    color_temp_kelvin: int | None = None,
    white_mode: bool = False,
) -> bytes:
    """构造 1~6 字节的 light_data。

    [0] = 0x80 | brightness(1..127)，关灯时只有 1 字节 0x00
    [1] = blue, [2] = red, [3] = green, [4] = warm, [5] = cold
    """
    if not is_on:
        return bytes([0x00])
    level = max(1, min(127, round(brightness * 127 / 255)))
    if white_mode:
        # 白光模式：固定 0x7F/0x7F（App 抓包为 ...7f7f），亮度同样带上
        return bytes([0x80 | level, 0x00, 0x00, 0x00, 0x7F, 0x7F])
    if color_temp_kelvin is not None:
        # mired -> 153..500 线性映射到 warm/cold
        if color_temp_kelvin <= 0:
            mired = 500
        else:
            mired = max(153, min(500, round(1_000_000 / color_temp_kelvin)))
        # 实测：CH 通道顺序在 HA 里是反的 —— 暖色（低色温/高 mired）要给 byte4 大值
        warm = round((mired - 153) * 255 / (500 - 153))
        cold = round((500 - mired) * 255 / (500 - 153))
        return bytes([0x80 | level, 0x00, 0x00, 0x00, warm & 0xFF, cold & 0xFF])
    if rgb is not None:
        red, green, blue = rgb
        return bytes([0x80 | level, blue & 0xFF, red & 0xFF, green & 0xFF, 0x00, 0x00])
    return bytes([0x80 | level])


def light_data_app(
    *,
    is_on: bool,
    brightness: int = 255,
    rgb: tuple[int, int, int] | None = None,
    color_temp_kelvin: int | None = None,
) -> bytes:
    """按 启明智云 App 反汇编得到的 7 字节 light_data（genSingleLightCommand）。

    [0]=开|亮度, [1]=blue, [2]=red, [3]=green, [4]=warm, [5]=cold, [6]=模式/标志
    - 纯开关 : [0x80|0 或 0x00, 0, 0, 0, 0xFF, 0xFF, 0xFF]
    - 亮度   : [0x80|b, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    - RGB    : [0xFF, blue*s, red*s, green*s, 0xFF, 0xFF, 0x80]（s=255/(r+g+b) 归一化）
    - 色温   : [0x80|b, 0, 0, 0, warm, cold, 0xFF]
    """
    if not is_on:
        return bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF])
    level = max(1, min(127, round(brightness * 127 / 255)))
    if rgb is not None:
        red, green, blue = rgb
        total = red + green + blue
        scale = 255.0 / total if total else 0.0
        return bytes(
            [
                0xFF,
                int(blue * scale) & 0xFF,
                int(red * scale) & 0xFF,
                int(green * scale) & 0xFF,
                0xFF,
                0xFF,
                0x80,
            ]
        )
    if color_temp_kelvin is not None:
        if color_temp_kelvin <= 0:
            mired = 500
        else:
            mired = max(153, min(500, round(1_000_000 / color_temp_kelvin)))
        warm = round((mired - 153) * 255 / (500 - 153))
        cold = round((500 - mired) * 255 / (500 - 153))
        return bytes([0x80 | level, 0x00, 0x00, 0x00, warm & 0xFF, cold & 0xFF, 0xFF])
    return bytes([0x80 | level, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])


# 灯泡要求命令帧的数据段补零到固定 12 字节（App 实测：开/关只发 3 字节有效数据，
# 但空口 data 段总是 12 字节，最后 9 字节是 0）。不足会**完全不响应**。
INNER_PADDING_LEN = 12


def single_control(light_id: int, mesh_key: bytes, data: bytes, **kwargs) -> bytes:
    inner = bytearray(INNER_PADDING_LEN)
    inner[0] = 2 | ((len(data) + 1) << 4)
    inner[1] = light_id & 0xFF
    inner[2:2 + len(data)] = data
    return generate_command(CMD_CONTROL, light_id, mesh_key, bytes(inner), **kwargs)


def group_control(group_id: int, mesh_key: bytes, data: bytes, **kwargs) -> bytes:
    inner = bytearray(len(data) + 4)
    inner[0] = ((len(data) + 3) << 4) | 3
    inner[1] = 0x2A
    inner[2] = 0xA8
    inner[3] = group_id & 0xFF
    inner[4:] = data
    return generate_command(CMD_CONTROL, 0, mesh_key, bytes(inner), **kwargs)


def set_group(light_id: int, mesh_key: bytes, group_id: int, **kwargs) -> bytes:
    inner = bytes([0x21, light_id & 0xFF, group_id & 0xFF])
    kwargs.setdefault("forward", False)
    return generate_command(CMD_CONTROL, light_id, mesh_key, inner, **kwargs)


def mesh_key_from_str(value: str) -> bytes:
    value = value.strip().replace(" ", "").replace("-", "").replace(":", "")
    if len(value) != 8:
        raise ValueError("mesh_key 必须是 8 位十六进制（4 字节）")
    return bytes.fromhex(value)


def payload_hex(payload: bytes) -> str:
    return payload.hex()


if __name__ == "__main__":
    import sys

    cases = [
        (1, "A1A2A3A4", "80", True),
        (1, "A1A2A3A4", "00", True),
        (1, "5E367BC4", "FF7F7F7F0000", True),
        (2, "11223344", "80FF00000000", True),
        (1, "A1A2A3A4", "A07F7F", False),
        (3, "DEADBEEF", "40", True),
    ]
    for lid, mk, ld, fwd in cases:
        data = bytes.fromhex(ld)
        key = mesh_key_from_str(mk)
        payload = single_control(lid, key, data, forward=fwd, sequence=0x2A)
        print(lid, mk, ld, int(fwd), payload.hex().upper())