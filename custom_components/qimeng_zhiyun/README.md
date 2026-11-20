# 启明智云 FastCon 蓝牙灯（Home Assistant 自定义集成）

把「启明智云 / brMesh / Broadlink FastCon」体系的 **BLE 蓝牙彩灯** 接入 Home Assistant。

- **灯泡**：只监听 BLE 广播（厂商 ID `0xFFF0`），不提供 GATT 控制，也不会上云。
- **App**：启明智云是纯本地 App（没有登录/云端功能），开场即扫描附近灯泡。
- **本集成**：在 HA 里完成「发现 + 协议帧构造 + 状态管理」，把算好的 payload 交给一台
  **ESP32 发射器**（ESPHome 固件，见 `/vol1/1000/docker/esphome/qimeng_fastcon_tx*.yaml`）广播出去。

> 为什么需要 ESP32：Home Assistant 本身（2026.11）**没有**发送 BLE 广播的能力，ESPHome 蓝牙代理
> 也只能收不能发。必须有一台能广播的设备，且要放在灯旁边。

```
┌──────────────┐   text.set_value(hex)   ┌──────────────┐    BLE adv (0xFFF0)   ┌────────┐
│ HA 集成      │ ──────────────────────► │ ESP32 发射器 │ ────────────────────► │ 彩灯   │
│ qimeng_zhiyun│                         │ (ESPHome)    │                       │        │
└──────┬───────┘                         └──────┬───────┘                       └────┬───┘
       │ 被动扫描（bluetooth 集成）              │ bluetooth_proxy（可选，低占空比）   │ 心跳广播
       └────────────────────────────────────────┴───────────────────────────────────┘
```

## 文件结构

| 文件 | 作用 |
| --- | --- |
| `fastcon.py` | **FastCon 协议核心**：帧构造 / 白化 / CRC16 / light_data 生成 / 反解（纯 Python，无第三方依赖） |
| `adv.py` | 收到的 BLE 厂商数据 → `BulbAdvertisement`（识别帧类型、解出 light_id/状态字节） |
| `transmitter.py` | 三种发射后端：`text` 实体（推荐）/ ESPHome 用户动作服务（实测 HA 不支持）/ 本机 BlueZ |
| `coordinator.py` | 一个 mesh 的运行时：灯泡表、序号、下发 |
| `light.py` | 灯实体（RGB + 色温，单向控制、状态为假设值） |
| `config_flow.py` | 配置/选项流程（mesh key + 发射器 + 手动加灯） |
| `services.py` | 调试服务 `qimeng_zhiyun.send_raw` / `qimeng_zhiyun.send_light` |

## 安装

1. 把 `qimeng_zhiyun/` 整个目录放进 `<config>/custom_components/`。
2. 用 `/vol1/1000/docker/esphome/qimeng_fastcon_tx.yaml` 编译好的 bin 刷 ESP32（放在灯旁边）。
3. HA → 设置 → 设备与服务 → 添加集成 → **启明智云 FastCon 蓝牙灯**：
   - **Mesh Key**：8 位十六进制，见下文
   - **发射器类型**：选 `ESPHome 发射器（text 实体）`
   - 下一步选固件生成的 `text.xxx_fastcon_payload` 实体
4. 保存后：灯泡在广播范围内会被**自动发现**；不在范围的可以在集成「配置」里**手动加灯**（填 App 里的「地址」，即 `light_id`）。

## 拿 Mesh Key

FastCon 的 mesh key 是 App 首次配网时生成的 4 字节密钥（同一「家庭」下所有灯共用一个），**不联网、不上云**。

方式一（推荐，手机 adb）：

```bash
adb logcat | grep -m 1 -o 'jyq_helper: .* key: *[0-9a-fA-F]\{8\}'
# 然后去 App 里开关一次灯，输出最后 8 位就是 mesh key
```

方式二（App「分享设备」）：分享时 App 会把 mesh key 写进分享数据的 protobuf 字段 `k` 上传，
用二维码里的 `bcs.pub/...?cloud=...&did=...&token=...` 串可反查（见 `/tmp/qm/analysis/probe.py --qr`）。

> 没有 mesh key：能**发现**灯泡（广播里带 did/name/type），但**控制不了**（帧里用 mesh key 加密）。

## 协议速查（`fastcon.py` 已实现并交叉验证）

- **数据段必须补零到固定 12 字节**（App 实测：开/关只有 3 字节有效数据，但空口 data 段总是 12 字节）。
  长度不足时**灯泡完全不响应** —— 这是实测踩到的最大的坑。
- header 低 4 位放 `light_id` 的低 4 位（App 行为，`addr_nibble=True` 默认）。
- 命令帧 `body = header(4) + data`：
  `header[0] = (light_id//256 & 0x0F) | (cmd_type<<4) | (forward?0x80:0)`，`header[1]=seq`，
  `header[2]=mesh_key[3]`，`header[3]=sum(除下标3外所有字节)`；
  header 4 字节 XOR `5E 36 7B C4`，data 部分 XOR `mesh_key[i&3]`。
- 包壳 `prepare_payload(addr=[0xC1,0xC2,0xC3], body)`：15 字节头 + `0x71,0x0F,0x55` + 倒序 addr +
  body + CRC16(LE)，对 `[0x0F, 0x0F+len(addr)+3)` 逐字节位反转，再 `whitening_init(0x25)` 白化，
  取 `[0x0F:]` 作为厂商数据。
- light_data（1~6 字节）：`[0x80|亮度(1..127), blue, red, green, warm, cold]`；关灯 `00`；
  白光 `..00 00 00 7F 7F`；色温按 mired 153..500 线性映射到 warm/cold。
- 单灯 inner：`[2|((len+1)<<4), light_id, ...light_data]`；组控 inner：`[((len+3)<<4)|3, 0x2A, 0xA8, group_id, ...]`。

交叉验证：本目录 `fastcon.py` 与 ESPHome 固件 C++ 实现（MIT `esphome-fastcon`）逐字节一致；
并且在联调中用 adb 抓到 App 真实空口广播反解，逐字节复现了 App 的开/关/颜色帧
（明文头 `D17E8274` / `D17D8241`，data `220180…` / `7201FF00FF…`）。

**已实测验证**（2026-11-21）：开灯、关灯、RGB 颜色、亮度、色温 均通过集成控制成功。

> 色温注意：本灯的两个白光通道与 HA 的「暖/冷」语义是**反向**的（第一版实现照抄公开实现，实测冷暖颠倒），
> 现在 `light_data()` 里已交换：低色温(K)→ `byte4`（暖）大值，高色温(K)→ `byte5`（冷）大值。

## 调试

```yaml
# 打开日志
logger:
  logs:
    custom_components.qimeng_zhiyun: debug
```

服务（开发者工具 → 动作）：

- `qimeng_zhiyun.send_light`：`light_id: 1`, `light_data: "80"` → 直接开灯全亮
- `qimeng_zhiyun.send_raw`：`payload: "<20~24 字节 hex>"` → 原样广播（验证发射器链路）

## 排查

| 现象 | 原因/处理 |
| --- | --- |
| 灯不动，日志有 `服务 ... 未找到` | 发射器类型选错了。HA 里 **没有** `esphome.*` 用户动作服务，必须用 `text` 实体后端 |
| `发射器实体不可用` | ESP32 离线，或 text 实体名选错 |
| 广播成功但灯不动 | mesh key 不对（换个 key 只能发现不能控）；灯泡断电；距离太远（调 `tx_power: 9`、`repeat_count: 2`） |
| 灯只偶尔响应 | 扫描占空比太高抢射频；把发射器固件的 `esp32_ble_tracker.scan_parameters.window` 调到 `50ms` 甚至关掉代理 |
| 自动发现看不到灯 | 灯还没广播过；把灯拿到发射器旁通电；或在 App 里点一下扫描唤醒 |
| 灯状态和 HA 显示不一致 | FastCon 是单向控制，灯泡不回报状态（心跳广播里若有状态字段，后续可解析同步） |

## 已知限制

- **单向控制**：灯泡不上报状态，HA 实体 `assumed_state: true`。
- **假定状态会持久化**：每次下发（开/关/亮度/颜色/色温）都会记到 `.storage/qimeng_zhiyun.<entry>.state`，
  HA 重启后恢复上次状态（不是掉回 off）。注意：在 App 里手动改灯，HA 不会知道 —— 协议限制。
- 每盏灯需要知道它在 App 里的「地址」（`light_id`）。本型号灯泡**不主动广播**（实测：手机 App 扫描
  只看到别的设备，看不到这盏灯，它只作接收端），所以自动发现对它不生效，需要在集成里手动加灯
  （填 `light_id`，本灯 = 1）。集成的蓝牙发现逻辑对其他会广播的 FastCon 设备仍然有效。
- 发射器必须靠近灯泡（BLE 广播距离），且同一 mesh 的所有灯需要同一把 mesh key。