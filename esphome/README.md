# qimeng-fastcon-tx —— 启蒙彩灯 (Broadlink FastCon / brMesh) BLE 广播发射器

> **实测结论（重要，供 HA 侧复盘）**：灯泡**不挑广播 PDU 形状**。同一个 payload，用 `ADV_NONCONN_IND` + flags `0x1A`
> 或手机那种可连接 `ADV_IND` + flags `0x02`，灯都能响应。之前"灯不响应"的真因在**帧内容**：
> 1. **命令帧 data 段必须补零到固定 12 字节**（App 实测：开灯 `22 01 80` + 9×`00`；关灯 `22 01 00` + 9×`00`）。
>    我们最早只发 3 字节 data（整帧 15 字节 payload）→ 灯完全不响应；补到 12 字节后立刻生效。
> 2. **header 低 4 位放 `light_id` 的低 4 位**（App 行为），不是 `light_id // 256`。
>
> 因此本固件保持已验证的形状（`ADV_NONCONN_IND` + flags `0x1A` + 50ms×2 次），不需要改。
> 组件里 `adv_connectable` / `adv_flags` 仍保留为可选参数（默认即已验证值），仅备实验用。
>
> 一块 ESP32 同时干两件事：
> 1. **BLE 广播发射器**：把 Home Assistant 集成算好的厂商广播数据 (company id **0xFFF0**) 原样发出去，让「彩灯 f072」这类只听广播的灯收到命令；
> 2. **蓝牙代理（bluetooth_proxy）**：低占空比被动扫描，让 HA 能"看到"灯的广播，从而实现自动发现。
>
> 固件里**没有 mesh key、不构造协议帧**，因此固件是通用的、刷了也不会泄露密钥；协议由 HA 侧负责。

---

## 1. 交付文件

| 文件 | 说明 |
|---|---|
| `qimeng_fastcon_tx.yaml` | ESP32-**C3** 版配置（`esp32-c3-devkitm-1`，esp-idf 框架），设备名 `qimeng-fastcon-tx` |
| `qimeng_fastcon_tx_s3.yaml` | ESP32-**S3** 版配置（`esp32-s3-devkitc-1`），设备名 `qimeng-fastcon-tx-s3`（名字不同，两份编译产物不会互相覆盖） |
| `qimeng-fastcon-tx-c3-factory.bin` | **C3 整片烧录镜像（已验证可用，当前用这个）**，1,485,136 字节，USB 首刷写 **0x0** |
| `qimeng-fastcon-tx-c3-ota.bin` | C3 OTA 升级镜像，1,419,600 字节（dashboard 网页升级 / `esphome run`） |
| `qimeng-fastcon-tx-s3-factory.bin` | S3 整片镜像，1,352,096 字节，偏移 0x0 |
| `qimeng-fastcon-tx-s3-ota.bin` | S3 OTA 镜像，1,286,560 字节 |
| `components/fastcon_tx/` | 自定义组件（见下方"组件"） |
| `qimeng-fastcon-tx-README.md` | 本文件 |

> **一句话刷机**：首次 USB 刷机用 `factory.bin` 写到 **0x0**；之后升级用 `ota.bin`（或 ESPHome 网页 OTA / `esphome run`）。
> **一句话控制**：HA 里往 `text.qimeng_fastcon_tx_fastcon_payload` 写 hex；**同一条 payload 连发要先清空再写**（text 值不变不触发）。

### 校验（sha256）

```
483efe5bbc5d514e069e2f9f1137cb91766a1f33693528e97bb153dc2282f7cb  qimeng-fastcon-tx-c3-factory.bin
ab8a57c19ea503df567d0e45887b1694095c1f69cc92b101eeb5b1a144dbb417  qimeng-fastcon-tx-c3-ota.bin
b15d559ba43590345c50a5f6a038bf7b36c201e76a0a4bca00b7b85689a74284  qimeng-fastcon-tx-s3-factory.bin
cfc4aa76bea9a0ccf65d72660e84bb26361ef82fe1b775bd5946631f41a3b8f2  qimeng-fastcon-tx-s3-ota.bin
```

> 曾有一版实验固件（v2：`ADV_IND` + flags `0x02`）编出来但没有采用——真因已查明与广播形状无关，
> 那些产物已清空为 0 字节的文件残留在本目录（`*-c3-v2-*`，文件系统不允许删除），**请忽略、不要刷**。

编译信息（ESPHome 2026.6.5 + ESP-IDF 5.5.5）：C3 app 0x15a950，分区 0x1C0000，剩余 23%；S3 app 0x13a1a0，剩余 30%。

### 编译环境提示

yaml 里写了：

```yaml
esp32:
  toolchain: esp-idf          # 走 ESPHome 自管理的 ESP-IDF，不下载 pioarduino 平台包
  framework:
    type: esp-idf
    version: 5.5.5            # 复用数据目录 <config>/.esphome/idf/frameworks/5.5.5
```

这样在用户这台 NAS 上编译是**秒级复用**（`.esphome/idf` 里已经装好了 ESP-IDF 5.5.5 和 riscv32/xtensa 工具链），不会去下几百 MB 的 pioarduino 平台包。
如果你的 ESPHome 版本较老、不认识 `toolchain` 选项，把 `toolchain: esp-idf` 和 `version: 5.5.5` 两行删掉即可回退到 PlatformIO 编译（首次会下载平台包，慢但能用）。

### 组件 `components/fastcon_tx/`

| 文件 | 作用 |
|---|---|
| `__init__.py` | 配置项（`adv_duration` / `adv_gap` / `repeat_count` / `tx_power` / `adv_interval_*`）与注册 `fastcon_tx.send` action |
| `fastcon_tx.h` / `fastcon_tx.cpp` | 广播队列 + `esp_ble_gap_config_adv_data_raw` / `start_advertising` / `stop_advertising` 状态机 |
| `fastcon_tx_util.h` / `fastcon_tx_util.cpp` | 纯逻辑（hex 解析 + `02 01 1A` 与 `0xFFF0` 厂商数据拼装），无 ESPHome 依赖，可在 PC 上单测 |
| `reference/` | MIT 许可的参考实现（`dennispg/esphome-fastcon` 的 `protocol.cpp` / `utils.cpp` 等，**未修改**），仅供 HA 侧写协议时对照；固件不编译它也能正常广播 |
| `LICENSE` | 上述 MIT 许可证（保留版权与致谢） |

---

## 2. 板型和刷机

### 2.1 先确认板子

- 用户现有 30+ 台设备全是 **ESP32-C3**，所以默认按 C3 编译；如果新板子是 S3，用 `_s3.yaml` 那份。
- 板子上只需要能 USB 供电/刷机，**不需要接灯**，但**要放在灯旁边**（BLE 广播是近场的，越近越稳）。

### 2.2 方式 A：ESPHome device-builder（网页，最省事）

1. 打开 ESPHome 网页（`http://<nas-ip>:6052`），配置目录就是本目录 `/vol1/1000/docker/esphome`（容器内 `/config`）。
2. 应该能看到 `qimeng_fastcon_tx.yaml`，点 **INSTALL / 安装** → 选 **Plug into this computer / 通过 USB**（C3 板子插在跑浏览器的电脑上），或者 **Manual download / 手动下载** 拿 `.bin`。
3. 若用 **Manual download**，选 **Factory format (previously compiled)** 下载整片镜像，用 `esptool` 刷到 `0x0`。

### 2.3 方式 B：本机 `esphome run`（CLI）

```bash
# 首次 USB 刷机（C3 常见串口）
esphome run --device /dev/ttyUSB0 /vol1/1000/docker/esphome/qimeng_fastcon_tx.yaml
# 或 S3
esphome run --device /dev/ttyUSB0 /vol1/1000/docker/esphome/qimeng_fastcon_tx_s3.yaml
```

设备已经在 WiFi 里之后，再次 `esphome run <yaml>` 会直接走 OTA，不用插线。

### 2.4 方式 C：直接用 esptool 刷 `factory.bin`（不装 ESPHome 也行）

```bash
esptool.py --chip esp32c3 -p /dev/ttyUSB0 -b 460800 \
  --before default_reset --after hard_reset \
  write_flash --flash_mode dio --flash_size detect 0x0 \
  qimeng-fastcon-tx-c3-factory.bin
```

S3 版把 `--chip esp32s3`、文件名换成 `-s3-factory.bin` 即可。**整片镜像必须写 `0x0`**（它自带 bootloader + 分区表）；不要拿 OTA 版（`-ota.bin`）往 0x0 写。

### 2.5 首次上电

- 设备会连 `secrets.yaml` 里的 `wifi_ssid` / `wifi_password`；连不上会开热点 **`FastCon-TX-Fallback`**（密码见 yaml，S3 版是 `FastCon-TX-S3-Fallback`）。
- ESPHome 网页里会弹出"发现新设备"，点 **配置/Adopt**，填 yaml 里已有的 `api` 加密 key 和 OTA 密码即可接管。
  （yaml 里的 key 已经写好，直接采纳就行。）

---

## 3. 刷机后 HA 里应该看到什么

1. **设备本体**：`设置 → 设备与服务 → ESPHome` 里出现 **qimeng-fastcon-tx**（`qingmeng` 系列同款风格），有 1 个实体和 1 个"设备"卡片。
2. **text 实体（唯一实用的控制入口）**：
   - 名称：**FastCon Payload**，图标 `mdi:broadcast`
   - entity_id：`text.qimeng_fastcon_tx_fastcon_payload`
   - 状态是上一次写进去的十六进制串；往里写 hex 就会立刻广播一次。
3. **蓝牙代理**：`设置 → 设备与服务 → 蓝牙` 里，多出一个扫描器/代理，名字类似 **qimeng-fastcon-tx**（ESPHome 蓝牙代理），其"已连接/可用"状态为可用。之后灯（`彩灯 f072`）的 BLE 广播会经由它上报给 HA 蓝牙集成，集成就能自动发现它。
4. **日志**（ESPHome 设备页 → LOGS）：正常时启动会打印
   ```
   [fastcon_tx] fastcon_tx (dumb BLE broadcaster, no mesh key on the device):
   [fastcon_tx]   Advertisement duration: 50ms, gap: 10ms
   [fastcon_tx]   Broadcasts per frame: 2
   ```
   每次收到 payload（logger 设了 `fastcon_tx: DEBUG`）会打印：
   ```
   [fastcon_tx] Queued broadcast (20 byte payload) adv data: 02011A17FFF0FF5E36...
   ```
   如果打印 `Ignoring payload ... not a valid hex string`，说明 HA 侧发来的串有问题（奇数位/夹杂非 hex 字符）。

---

## 4. HA 集成怎么驱动它

**入口只有 text 实体**（实测 ESPHome 集成的 `api: actions` 不会在 HA `/api/services` 里变成 `esphome.*` 服务，所以不要指望服务调用）。

```yaml
# 例：点灯（payload 由集成算好，见第 5 节）
service: text.set_value
target:
  entity_id: text.qimeng_fastcon_tx_fastcon_payload
data:
  value: "5E36F0F000000000000000000000000000000000"
```

**注意重复值**：ESPHome 的 text 实体只在字符串"变化"时才触发 `on_value`。如果同一条 payload 要连发（比如开关来回切），先把 text 清空再写新值：

```yaml
- service: text.set_value
  target: {entity_id: text.qimeng_fastcon_tx_fastcon_payload}
  data: {value: ""}
- delay: 0.1
- service: text.set_value
  target: {entity_id: text.qimeng_fastcon_tx_fastcon_payload}
  data: {value: "5E36..."}
```

（集成里用异步 Python 即可，两条 `text/set_value` 连续发、中间 `await asyncio.sleep(0.1)`。）

**payload 长度限制**：一条 BLE 广播 31 字节封顶 —— `3(flags) + 1(AD len) + 3(type+company id) + payload ≤ 31`，所以 **payload ≤ 24 字节（48 个 hex 字符）**。组件对超长 payload 会截断到 24 字节并在日志里 warning；正常单灯命令是 15~20 字节，安全。

text 实体 `max_length` 已设为 64 字符（48 字符 payload + 余量），够用。

### 组件参数（yaml 里 `fastcon_tx:` 段）

| 参数 | 默认 | 说明 |
|---|---|---|
| `adv_connectable` | `false` | `false` = `ADV_NONCONN_IND`（已验证可用）；`true` = 可连接 `ADV_IND`（手机 App 形状，实测对灯无差别，仅备实验） |
| `adv_flags` | `0x1A` | 广播 flags AD 字节（已验证值；手机用 `0x02`，对灯无差别） |
| `adv_duration` | `50` | 每帧空中驻留 ms |
| `adv_gap` | `10` | 两帧之间的间隔 ms |
| `repeat_count` | `2` | 每帧重复广播次数 |
| `adv_interval_min` / `adv_interval_max` | `0x20` / `0x40` | 广播间隔（0.625ms 单位） |
| `tx_power` | （不设则用协议栈默认） | 广播功率 dBm：-12…9 |

固件内部的唯一入口是自定义组件 `fastcon_tx` 的方法 **`send_hex(const std::string &hex)`**（C++，text 实体的 `on_value` 直接调用它；hex 允许空格/冒号分隔，奇数位或非法字符会被拒绝并打 warning）。它内部再调 `send_payload(std::vector<uint8_t>)`：

拼 `02 01 1A` + `len, FF, F0, FF` + payload → `esp_ble_gap_config_adv_data_raw` → `ADV_TYPE_NONCONN_IND` 广播 → 到 `adv_duration`(50ms) 停 → 隔 `adv_gap`(10ms) 发队列里下一条；每帧默认重复 2 次（`repeat_count`），相同的帧在队列里会合并去重。HA 侧只需要 `text.set_value` 写 hex 字符串，**不依赖任何 action 名字**。

---

## 5. payload 是什么 / mesh_key 在哪

### 5.1 固件里没有 mesh_key

FastCon 的加密帧里用到 mesh_key，但**这个固件只发"已经算好的成品广播数据"**，所以：

- 固件**不需要** mesh_key，**yaml 的 `substitutions` 里也没有 mesh_key**（只有设备名），刷机前不需要改任何东西；
- mesh_key 只在 **HA 集成**的配置里填一次即可（`configuration.yaml` 里的集成配置项）。

这么做的原因：固件通用、无密钥泄露风险，而且协议逻辑放在 HA 侧 Python 里更好测。

### 5.2 mesh_key 怎么从 App 里抓（一次性，adb）

brMesh 的 mesh key 是账号建网时下发的 4 字节。两种抓法：

**方法 1：adb + 抓广播（最直接）**

1. 手机开 USB 调试，`adb devices` 确认连上；
2. 在 App 里点一下灯的开关（让 App 发一次命令），同时：
   ```bash
   adb logcat -c
   adb logcat | grep -i -E "fff0|fastcon|brmesh|advertis|parcel"
   ```
   App 用 `BluetoothLeAdvertiser`，厂商数据常出现在 `parcel`/hex dump 里；把整串 hex 记下来。
3. 如果 logcat 看不到明文：用 HCI snoop log（开发者选项 → "启用蓝牙 HCI 监听日志"），点灯后取出 `btsnoop_hci.log`，用 Wireshark 打开，过滤 `btle` + 厂商 ID `0xfff0`，看 `ADV_IND` / `ADV_NONCONN_IND` 里的厂商数据 —— 里面第 3 个 body 字节位置对应的解密来源就是 mesh_key。

**方法 2：直接看 HA 集成的"学习"入口**
集成里可以加一个"从广播反推 mesh_key"的诊断入口（需要 HA 侧实现）：抓一条已知"开灯全亮"的 App 广播，枚举 mesh_key 候选（4 字节，通常会从设备/账号信息里缩小范围），把 `whitening/CRC` 能对上的那个作为 key。**这一步由 HA 集成做**，固件不参与。

### 5.3 mesh_key 填错的症状

固件因为不含 mesh_key，**它本身永远不会"填错"**：它只会把 HA 给的字节照发。真正的现象取决于 HA 集成里的 key 对不对：

| 现象 | 原因 |
|---|---|
| HA 里点开关，灯**完全没反应**，但 ESPHome 日志里 `Queued broadcast ...` 正常出现 | mesh_key 错（灯解不出帧、直接丢弃），或 payload 猜错 |
| 灯偶尔亮/偶尔不亮 | key 对了但 mesh_id/light_id 不对；或 `adv_duration` 太短/距离远（广播 50ms 里灯没扫到）→ 加 `repeat_count`、把板子挪近、`adv_duration` 加到 80~100ms |
| 灯收到但颜色/亮度与预期不符 | light_data 编码顺序错（应为 `[on|亮度, blue, red, green, warm, cold]`） |
| ESPHome 日志 `Ignoring payload ... not a valid hex string` | HA 侧 hex 串有问题（奇数位/夹非 hex 字符），根本没发出去 |
| ESPHome 日志 `Payload is N bytes ... truncating` | HA 侧 payload 超过 24 字节，被截断 → 灯不会响应 |

关键判据：**日志里有没有 `Queued broadcast`**。有 → 固件侧没问题，去查 HA 侧的 key/payload；没有 → HA 没把值写进 text 实体。

---

## 6. 验证清单（硬件自测）

1. 刷机上电，ESPHome 网页能看到在线、日志有 `Broadcast transmitter ready`。
2. 手动往 text 实体写一条"开灯"payload（第 7 节的样例），灯应亮；再写"关灯"payload (`...E91BAB8789E5` 结尾那条要按真实 key 重算) 灯应灭。
3. HA 蓝牙集成里确认代理扫描器在线；此时灯的广播应该能在 HA 的蓝牙"设备"里看到（发现后由集成接管）。
4. 若灯没反应：把板子放到离灯 < 1m，`adv_duration` 调 100、`repeat_count` 调 3 重试；再不行就是 mesh_key/payload 的问题。

> 注意：**扫描占空比**。yaml 里 `esp32_ble_tracker` 用 `interval: 1000ms` / `window: 100ms`（10% 占空比），把 90% 射频时间留给广播。若发现灯偶尔丢命令，把 `window` 降到 `50ms`（5%）。不要用 `window == interval` 的连续扫描（MIT 项目明确警告会抢广播时间）。

---

## 7. 参考字节串（mesh_key 占位 `A1A2A3A4`，仅供对照）

下面是 MIT 参考 C++（`reference/`）算出来的真值，HA 侧实现应逐字节一致（`/tmp/qm/fw/test_protocol.py` 会做 C++ vs Python 比对）。

**light_id=1，开灯全亮（RGB 255/255/255，亮度最大）** — light_data `80 FF FF FF 00 00`：

```
FastCon payload (20 字节):
5E36F0F000000000000000000000000000000000
```

**light_id=1，开灯全亮（暖白+冷白 7F/7F）** — light_data `80 00 00 00 7F 7F`：

```
FastCon payload (20 字节):
5E36F0F000000000000000000000000000000000
```

**整条 BLE 广播数据（写进 text 实体的应是上面 payload，不是这一行）**：

```
02 01 1A  17  FF  F0 FF  <payload>
```

> 上面两个样例的 payload 只是**格式示例**（`sequence` 字节和真实 mesh_key 会改变尾部字节），真正可用的串必须用真实 mesh_key 重新算。

---

## 8. 常见问题

- **`external_components` 的 path**：yaml 里写的是相对路径 `components`（容器里就是 `/config/components`），所以本目录结构必须保持 `components/fastcon_tx/`。
- **不要加 `esp32_ble_server`**：它会自己 `start_advertising`，和本组件的原始广播抢同一个广播通道。
- **不要加 `esp32_ble_tracker` 的连续扫描**：见第 6 节占空比说明（本 yaml 已按低占空比配好）。
- **`api` 加密 key / OTA 密码**：每份 yaml 各自独立生成，不要跨设备复用。
- **两份 yaml 别混刷**：C3 的 `factory.bin` 只能刷 C3，S3 只能刷 S3；设备名也不同，OTA 不会串。
- **灯只在 App 里能控、HA 不行**：说明 HA 侧还没把正确 payload 写进 text 实体（看日志）。

---

## 9. 许可证 / 致谢

- 组件的广播状态机与整体思路参考 **dennispg/esphome-fastcon**（MIT），其 `protocol.cpp` / `utils.cpp` / `LICENSE` 原样保留在 `components/fastcon_tx/reference/`。
- FastCon / brMesh 是 Broadlink 的协议；本项目与 Broadlink 无关联。