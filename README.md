# 启明智云 FastCon 蓝牙灯 · Home Assistant 集成

把 **启明智云 / brMesh / Broadlink FastCon** 体系的 **BLE 蓝牙灯** 接入 Home Assistant：
开关、亮度、RGB、色温，全部可用；**灯不联网、不上云**，完全本地控制。

![logo](custom_components/qimeng_zhiyun/brand/logo.png)

---

## 它是怎么工作的

这类灯是 **FastCon / brMesh** 协议：灯泡只**监听 BLE 广播**（厂商 ID `0xFFF0`），
没有 GATT 控制通道，也不会上报状态；手机 App 是把命令**当广播发出去**的。

Home Assistant 本身（含 2026.11）**没有**发送 BLE 广播的能力，ESPHome 蓝牙代理也只能收不能发。
所以整套方案是：

```
┌──────────────────┐   text.set_value(hex)   ┌────────────────┐   BLE adv (0xFFF0)   ┌──────┐
│ HA 集成          │ ──────────────────────► │ ESP32 发射器   │ ───────────────────► │ 彩灯 │
│ qimeng_zhiyun    │                         │ (ESPHome 固件) │                      │      │
└────────┬─────────┘                         └────────┬───────┘                      └──┬───┘
         │ 蓝牙被动扫描（可选，用于发现）              │ 同时可做蓝牙代理（收广播）          │
         └───────────────────────────────────────────┴──────────────────────────────────┘
```

- 集成负责：协议帧构造（白化/CRC/加密）、灯的实体与状态、把 payload 交给发射器
- ESP32 固件只做一件事：把收到的一串十六进制 payload 原样作为 BLE 厂商数据广播出去（**固件里没有密钥**）

## 功能

- 开 / 关 / 亮度 / RGB / 色温（**已实灯验证**）
- **多发射器**：可以同时选多台 ESP32（全屋代理一起广播，覆盖更好）
- **假设状态 + 持久化**：单向设备，状态以 HA 最后一次下发为准，重启后恢复
- 蓝牙被动发现（对会广播的 FastCon 设备），以及「手动加灯」
- 调试服务 `qimeng_zhiyun.send_light` / `qimeng_zhiyun.send_raw`

## 安装

### 方式一：HACS 自定义仓库（推荐）

1. HACS → 集成 → 右上角 ⋮ → **自定义仓库**：填本仓库地址，类别选 **Integration**
2. 安装 **启明智云 FastCon 蓝牙灯**，重启 Home Assistant

### 方式二：手动

把 `custom_components/qimeng_zhiyun/` 整个目录复制到 `<config>/custom_components/`，重启 HA。

> 依赖：HA 的 `bluetooth` 集成（本机适配器或 ESPHome 蓝牙代理）。没有蓝牙硬件也能用，只是少了自动发现。

## 准备：Mesh Key（必须）

控制命令用 **4 字节 mesh key**（8 位十六进制）加密。它由 App 首次配网时随机生成，**不会上传云端**。

手机 adb 抓（最简单）：

```bash
adb logcat | grep -m 1 -o 'jyq_helper: .* key: *[0-9a-fA-F]\{8\}'
# 然后在 App 里开关一次灯，输出最后 8 位就是 mesh key
```

也可以在 App「分享设备」二维码里导出（protobuf 字段 `k`）。

## 准备：ESP32 发射器

`esphome/` 目录里有现成配置（ESP32-C3 / S3）和自定义组件 `fastcon_tx`：

```yaml
# 关键部分（完整见 esphome/qimeng_fastcon_tx.yaml）
external_components:
  - source: { type: local, path: components }
    components: [fastcon_tx]

fastcon_tx:
  id: fastcon_tx_1
  adv_duration: 50
  adv_gap: 10
  repeat_count: 2
  tx_power: 9

text:
  - platform: template
    id: fastcon_payload
    name: "FastCon Payload"      # HA 里就是 text.<device>_fastcon_payload
    optimistic: true
    mode: text
    max_length: 64
    on_value:
      - lambda: |-
          id(fastcon_tx_1).send_hex(x);
```

刷好后把它**放在灯旁边**（BLE 广播是近场的）。它会同时在 HA 里出现：

- `text.xxx_fastcon_payload`：发射器入口（集成会写它）
- 一个蓝牙代理（可在别的房间做扫描）

## 配置集成

HA → 设置 → 设备与服务 → **添加集成** → **启明智云 FastCon 蓝牙灯**：

1. **Mesh Key**：上一步拿到的 8 位 hex
2. **发射器类型**：`ESPHome 发射器（text 实体，可多选）`
3. 选择发射器实体（可多选，`text.xxx_fastcon_payload`）
4. 保存后，灯可以手动添加（填 App「设备详情」里的**地址**，即 `light_id`），
   或者如果灯会广播，会自动出现

## 协议速查（`custom_components/qimeng_zhiyun/fastcon.py`）

- 命令帧：`body = header(4) + data`，`header[0]` 低 4 位放 `light_id` 的低 4 位、高 4 位放命令类型（`5`）、
  `0x80` 为 forward 位，`header[1]` 序号、`header[2] = mesh_key[3]`、`header[3]` 为校验和
- header 四字节 XOR `5E 36 7B C4`，data 段逐字节 XOR `mesh_key[i & 3]`
- 包壳：15 字节头 + `71 0F 55` + 倒序 `C1 C2 C3` + body + CRC16(LE)，位反转 + `whitening_init(0x25)` 白化，
  取 `[0x0F:]` 放进 `0xFFF0` 厂商数据
- **data 段必须补零到固定 12 字节**（App 实测：开/关只有 3 字节有效数据，但空口永远 12 字节）——
  长度不足时灯泡**完全不响应**，这是本项目踩得最深的坑
- light_data：`[0x80|亮度, blue, red, green, warm, cold]`，关灯 `00`；
  色温映射注意本灯**暖/冷通道与 HA 语义相反**（已在代码里交换）

> 帧格式与实机 App 的空中广播逐字节核对过（含 CRC/校验和）。

## 已知限制

- **单向控制**：灯泡不回报状态，实体状态为「HA 最后一次下发」的假定值
- 本型号灯泡**不主动广播**，所以自动发现对它不生效，需要手动填 `light_id`
- 需要一台靠近灯的 ESP32 做发射器（HA 自身无法发广播）

## 致谢

- [esphome-fastcon](https://github.com/dennispg/esphome-fastcon)（MIT）——FastCon 协议公开实现，本仓库的
  ESP32 发射器组件包含其 `reference/` 原样代码与 LICENSE
- [Mooody 的 FastCon 逆向文章](https://mooody.me/posts/2023-04/reverse-the-fastcon-ble-protocol/)
- 以及 [Home Assistant 社区帖](https://community.home-assistant.io/t/brmesh-app-bluetooth-lights/473486) 里的各位贡献者

## License

MIT，见 [LICENSE](LICENSE)。`esphome/components/fastcon_tx/reference/` 保留上游 MIT 许可。