# 启明智云 FastCon 蓝牙灯 · Home Assistant 集成

把 **启明智云 / brMesh / Broadlink FastCon** 体系的 **BLE 蓝牙灯** 接入 Home Assistant：
开关、亮度、RGB、色温，全部可用；**灯不联网、不上云**，完全本地控制。

![logo](custom_components/qimeng_zhiyun/brand/logo.png)

---

## 它是怎么工作的

这类灯是 **FastCon / brMesh** 协议：灯泡只**监听 BLE 广播**（厂商 ID `0xFFF0`），
没有 GATT 控制通道，也不会上报状态；手机 App 是把命令**当广播发出去**的。

Home Assistant 自身（含 2026.11）**没有**"发送 BLE 广播"的接口（`bluetooth` 集成只有扫描/发现；
ESPHome 蓝牙代理也只能收不能发）。所以必须有一个**能广播的角色**：

```
┌──────────────────┐   把算好的 hex 交给发射器   ┌────────────────┐   BLE adv (0xFFF0)   ┌──────┐
│ HA 集成          │ ─────────────────────────► │ 发射器          │ ──────────────────► │ 彩灯 │
│ qimeng_zhiyun    │                            │ ESP32 / 本机蓝牙 │                      │      │
└────────┬─────────┘                            └────────┬───────┘                      └──┬───┘
         │ 蓝牙被动扫描（可选，用于发现）                  │ 也可同时做蓝牙代理（收广播）      │
         └──────────────────────────────────────────────┴──────────────────────────────────┘
```

- 集成负责：协议帧构造（白化 / CRC / 加密）、灯实体与状态、把 payload 交给发射器
- 发射器只做一件事：把一串十六进制 payload 原样作为 BLE 厂商数据广播出去（**固件里没有密钥**）

## 功能

- 开 / 关 / 亮度 / RGB / 色温（**已实灯验证**）
- **三种发射器后端，可混用**：ESPHome 直连 API（推荐）/ ESPHome text 实体 / HA 主机本地蓝牙（BlueZ）
- **多发射器扇出**：同时交给多台设备（全屋代理一起广播，覆盖更好）
- **假设状态 + 持久化**：单向设备，状态以 HA 最后一次下发为准，重启后恢复
- 蓝牙被动发现（对会广播的 FastCon 设备）、手动加灯、调试服务

## 安装

> **Home Assistant 2026.11 起安装方式有变化**：HA 内置了 **Marketplace**（市场），
> **HACS 从 2026.11 起已经装不上了**（HACS 不再兼容新版本，市场功能由内置 Marketplace 取代）。
>
> 也就是说：
> - **2026.11 及以上**：用 **Marketplace**（在 HA 里直接添加自定义仓库）或手动复制
> - **2026.11 之前**：用 HACS，或手动复制

### 方式一：Marketplace（HA **2026.11 及以上**）

1. 打开 **Marketplace**（HA 2026.11 起内置，不需要安装 HACS；入口在 HA 的设置/市场）
2. 首次使用需要 **连接 GitHub**：点继续 → 打开给出的网址 → 输入设备码 → 授权。
   （只读取公开信息、不需要密码，可随时在 GitHub 撤销；不连接也能浏览官方目录，
   但**添加自定义仓库需要连接**，否则受 GitHub 匿名请求频率限制）
3. Marketplace → **添加自定义仓库** → 填 `hlhk2017/qimeng_zhiyun`
4. 安装 **启明智云 FastCon 蓝牙灯** → 重启 Home Assistant

> 仓库根目录带 `hacs.json`（Marketplace 要求），且**不使用 ZIP release**（Marketplace 不支持从
> zip release 安装集成），因此可以直接从仓库内容安装到 `custom_components/qimeng_zhiyun/`。

### 方式二：HACS（仅 **2026.11 之前**）

> ⚠️ **HA 2026.11 及以后 HACS 无法安装**（不再兼容），请用上面的 Marketplace；下面步骤只对旧版本有效。

1. HACS → 集成 → ⋮ → **自定义仓库**：填本仓库地址，类别选 **Integration**
2. 搜索安装 **启明智云 FastCon 蓝牙灯** → 重启 HA

### 方式三：手动

把 `custom_components/qimeng_zhiyun/` 整个目录复制到 `<config>/custom_components/`，重启 HA。

> 依赖：HA 的 `bluetooth` 集成（本机适配器或 ESPHome 蓝牙代理）。没有蓝牙硬件也能用，只是少了自动发现。

## 准备 1：Mesh Key（必须）

控制命令用 **4 字节 mesh key**（8 位十六进制）加密。它由 App 首次配网时随机生成，**不上传云端**。

手机 adb 抓（最简单）：

```bash
adb logcat | grep -m 1 -o 'jyq_helper: .* key: *[0-9a-fA-F]\{8\}'
# 然后在 App 里开关一次灯，输出最后 8 位就是 mesh key
```

也可以在 App「分享设备」二维码里导出（protobuf 字段 `k`）。

## 准备 2：选一个发射器

### 情况 A：用一台 ESP32（推荐，已实机验证）

`esphome/` 里有现成配置（ESP32-C3 / S3）和自定义组件 `fastcon_tx`：

```yaml
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

刷好后把它**放在灯旁边**（BLE 广播是近场的）。它还能顺便当蓝牙代理。

集成里选 **`ESPHome 设备（直连 API，最稳）`**，填 `IP|API加密key`（一行一台，key 可留空）：

```
192.168.2.31|vRn8TAVnC7l9P/sDetumzCvH7bJ3HM+3u4mR9yiRefs=
192.168.2.52|ZSWk3q8YpOG0QaUT6TQrzMwp6Uzh/L+YoNxx4vKGEzQ=
```

> 为什么推荐 API 直连而不是 text 实体：HA 的 ESPHome 集成里由 mDNS 发现来的条目经常是
> `not_loaded`（本机 33 个条目里有 29 个这种状态），此时 text 实体不可用；
> 但设备的 6053 端口其实是通的，直连 API 不受影响。
> 其它两种后端：`ESPHome 发射器（text 实体）`（依赖 HA 的 ESPHome 集成）、
> `本机蓝牙适配器（BlueZ）`（见情况 B）。

### 情况 B：**HA 主机自带蓝牙适配器**（不接 ESP32）

如果 HA 主机（或虚拟机）本身有 **Bluetooth 4.0+ 适配器**、且 BlueZ 正常（`bluetoothctl` / `hcitool` 可看到
`hci0`），可以完全不用 ESP32：集成里的 **`BlueZ` 后端**会通过 D-Bus 调
`org.bluez.LEAdvertisingManager1`，直接向适配器注册一条 `0xFFF0` 厂商数据广播。

要求与注意：

- HA 容器需要能访问宿主 BlueZ：`/run/dbus` 挂载 + （实测）`privileged: true`，宿主 `bluetoothd` 在运行
- 适配器要支持 **LE Advertising**（绝大多数 USB 蓝牙 4.0+ 适配器可以；只能扫描的芯片不行）
- BlueZ 支持**多广播实例**，可以和 HA 的蓝牙扫描/ESPHome 代理共存；但要和其它应用抢广播槽位时可能失败
- 集成里 `发射器类型` 选 **`本机蓝牙适配器（BlueZ）`**，适配器名填 `hci0`
- 我们只在**没有蓝牙硬件的 NAS**上验证过其余两条路径，BlueZ 后端是**实验性**的（代码已实现，
  失败会明确报错），欢迎反馈日志

> 一句话：**主机有蓝牙适配器 → 可以纯 HA 方案；没有 → 用 ESP32 当发射器**。两种都能和
> "HA 蓝牙扫描 / ESPHome 代理"共存。

### 三种后端对比

| 后端 | 需要什么 | 依赖 HA ESPHome 集成? | 状态 |
| --- | --- | --- | --- |
| ESPHome 设备（直连 API） | 一台刷好 `fastcon_tx` 的 ESP32 + 它的 IP / api key | ❌ 不需要 | ✅ 实机验证 |
| ESPHome 发射器（text 实体） | 同上，但要求该设备在 HA 里 `loaded` | ✅ 需要 | ✅ 可用 |
| 本机蓝牙适配器（BlueZ） | HA 主机有 BT 适配器 + dbus | ❌ 不需要 | 🧪 实验性 |

## 配置集成

HA → 设置 → 设备与服务 → **添加集成** → **启明智云 FastCon 蓝牙灯**：

1. **Mesh Key**：准备 1 拿到的 8 位 hex
2. **发射器类型**：见上表
3. 按类型填发射器（API 直连：`IP|key` 多行；text 实体：多选；BlueZ：`hci0`）
4. 保存后手动加灯：填 App「设备详情」里的**地址**（`light_id`，截图那台是 `1`）；
   会广播的 FastCon 设备也会被自动发现

## 协议速查（`custom_components/qimeng_zhiyun/fastcon.py`）

- 命令帧 `body = header(4) + data`：`header[0]` 低 4 位 = `light_id` 低 4 位、高 4 位 = 命令类型（5）、
  `0x80` 为 forward；`header[1]` 序号；`header[2] = mesh_key[3]`；`header[3]` = 校验和
- header 4 字节 XOR `5E 36 7B C4`，data 逐字节 XOR `mesh_key[i & 3]`
- 包壳：15 字节头 + `71 0F 55` + 倒序 `C1 C2 C3` + body + CRC16(LE)，位反转 + `whitening_init(0x25)`，
  取 `[0x0F:]` 放进 `0xFFF0` 厂商数据
- **data 段必须补零到固定 12 字节**（App 实测：开/关只有 3 字节有效数据，但空口永远 12 字节）——
  长度不足时灯泡**完全不响应**，这是本项目踩得最深的坑
- light_data：`[0x80|亮度, blue, red, green, warm, cold]`，关灯 `00`；
  色温映射注意本灯**暖/冷通道与 HA 语义相反**（已在代码里交换）
- 帧字节与实机 App 空中广播逐字节核对过（含 CRC / 校验和）

## 已知限制

- **单向控制**：灯泡不回报状态，实体状态为「HA 最后一次下发」的假定值（已做持久化，重启恢复）
- 本型号灯泡**不主动广播**，所以自动发现对它不生效，需要手动填 `light_id`
- 需要"能广播的角色"：ESP32 或主机自带蓝牙适配器

## 致谢

- [esphome-fastcon](https://github.com/dennispg/esphome-fastcon)（MIT）——FastCon 协议公开实现，
  本仓库的 ESP32 发射器组件包含其 `reference/` 原样代码与 LICENSE
- [Mooody 的 FastCon 逆向文章](https://mooody.me/posts/2023-04/reverse-the-fastcon-ble-protocol/)
- [Home Assistant 社区帖](https://community.home-assistant.io/t/brmesh-app-bluetooth-lights/473486) 的各位贡献者

## License

MIT，见 [LICENSE](LICENSE)。`esphome/components/fastcon_tx/reference/` 保留上游 MIT 许可。
