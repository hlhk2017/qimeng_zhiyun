# qimeng-fastcon-tx 自测报告

## 1. 固件侧（hex 解析 + 广播数据拼装，宿主机 g++ 编译真实组件代码）

| 输入 payload | 广播数据 (hex) | 截断 | 总字节 |
|---|---|---|---|
| `80` | `02011A04FFF0FF80` | False | 8 |
| `80ff00000000` | `02011A09FFF0FF80FF00000000` | False | 13 |
| `5e36f0f000000000000000000000000000000000` | `02011A17FFF0FF5E36F0F000000000000000000000000000000000` | False | 27 |
| `00112233445566778899AABBCCDDEEFF0011223344556677` | `02011A1BFFF0FF00112233445566778899AABBCCDDEEFF0011223344556677` | False | 31 |
| `00112233445566778899AABBCCDDEEFF001122334455667788` | `02011A1BFFF0FF00112233445566778899AABBCCDDEEFF0011223344556677` | True | 31 |

- parse_hex(`80`) -> ok 80
- parse_hex(`80ff00000000`) -> ok 80FF00000000
- parse_hex(`80 FF 00 00 00 00`) -> ok 80FF00000000
- parse_hex(`02:01:1a`) -> ok 02011A
- parse_hex(`8`) -> bad 
- parse_hex(`zz`) -> bad 
- parse_hex(``) -> ok 

## 2. FastCon 帧构造（HA 侧要做的事）：MIT 参考 C++ vs Python 复刻

mesh_key 占位 = `A1A2A3A4`（正式刷机前用 adb 从 App 抓到真的再填进 HA 集成）

### light 1, 开灯全亮 (RGB 255,255,255)
- light_data: `80FFFFFF0000`   sequence: 0
- FastCon 厂商 payload (20 字节):
  `6DB64368931DDD05071A4BAB079065A1D207D98F`
- 完整 BLE 广播数据 (27 字节):
  `02011A17FFF0FF6DB64368931DDD05071A4BAB079065A1D207D98F`
- C++ vs Python: 一致

### light 1, 开灯全亮 (白光 7f/7f)
- light_data: `800000007F7F`   sequence: 0
- FastCon 厂商 payload (20 字节):
  `6DB64368931DDD05071B4BAB076F9A5EAD7823CC`
- 完整 BLE 广播数据 (27 字节):
  `02011A17FFF0FF6DB64368931DDD05071B4BAB076F9A5EAD7823CC`
- C++ vs Python: 一致

### light 1, 关灯
- light_data: `00`   sequence: 0
- FastCon 厂商 payload (15 字节):
  `6DB64368931DDD0507E91BAB8789E5`
- 完整 BLE 广播数据 (22 字节):
  `02011A12FFF0FF6DB64368931DDD0507E91BAB8789E5`
- C++ vs Python: 一致

### light 1, 开灯亮度 50%
- light_data: `C0`   sequence: 0
- FastCon 厂商 payload (15 字节):
  `6DB64368931DDD0507291BAB475C18`
- 完整 BLE 广播数据 (22 字节):
  `02011A12FFF0FF6DB64368931DDD0507291BAB475C18`
- C++ vs Python: 一致

## 结论

全部通过：
- 固件 hex 解析（含空格/冒号分隔、奇数位、非法字符）正确
- 广播数据 = `02 01 1A` + `len` + `FF F0 FF` + payload；24 字节 payload 刚好 31 字节，25 字节被截断到 24
- HA 侧 FastCon 帧构造与 MIT 参考 C++ 逐字节一致
