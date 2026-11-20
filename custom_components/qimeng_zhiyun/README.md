# 启明智云 FastCon 蓝牙灯（HA 自定义集成）

把 **启明智云 / brMesh / Broadlink FastCon** 的 BLE 蓝牙灯接入 Home Assistant：开关、亮度、RGB、色温。
灯不联网、不上云，完全本地控制。

> 完整文档（安装 / mesh key / 发射器固件 / 协议 / 排查）见仓库根目录 **README.md**。

## 安装

- **HA 2026.11+**：内置 **Marketplace** → 添加自定义仓库 `hlhk2017/qimeng_zhiyun` → 安装（需先连接 GitHub）
- **HA 2026.11 之前**：HACS → 自定义仓库（类别选 Integration）→ 安装
- 或手动把本目录复制到 `<config>/custom_components/qimeng_zhiyun/`

## 配置需要三样东西

1. **Mesh Key**：8 位十六进制（App 配网时生成，用 adb 抓 `jyq_helper ... key: XXXXXXXX`）
2. **发射器**（三选一，可混用）：
   - **ESPHome 设备（直连 API，最稳）**：`IP|api加密key`，一行一台 —— 需刷仓库 `esphome/` 里的固件
   - **ESPHome 发射器（text 实体）**：依赖 HA 的 ESPHome 集成里该设备是 `loaded`
   - **本机蓝牙适配器（BlueZ）**：HA 主机自带 Bluetooth 4.0+ 适配器时可用，实验性
3. **灯的 light_id**（App「设备详情」里的「地址」，1~255）

## 目录

| 文件 | 作用 |
| --- | --- |
| `fastcon.py` | 协议核心：帧构造 / 白化 / CRC16 / light_data（含 App 实测的 12 字节补零） |
| `adv.py` | 收到的 BLE 厂商数据 → 解析成灯泡信息 |
| `transmitter.py` | 三种发射后端：ESPHome 直连 API / text 实体 / BlueZ，含多发射器扇出 |
| `coordinator.py` | mesh 运行时：灯表、序号、下发、状态持久化 |
| `light.py` | 灯实体（RGB + 色温，单向控制、假定状态持久化） |
| `config_flow.py` / `strings.json` / `translations/` | 配置与选项流程（中文/英文） |
| `services.py` | 调试服务 `qimeng_zhiyun.send_light` / `send_raw` |
| `brand/` | 品牌图（取自 App 的 logo） |

## 关键坑（都已在代码里处理）

- **data 段必须补零到 12 字节**：App 实测开/关只有 3 字节有效数据，空口永远 12 字节；少一个字节灯泡就完全不理
- **header 低 4 位是 light_id 低 4 位**（不是 `light_id // 256`）
- **色温的暖/冷通道与 HA 语义相反**，已交换
- HA 的 ESPHome 集成里 mDNS 发现来的条目可能长期 `not_loaded`，所以推荐"直连 API"后端

## 已知限制

- 单向控制：灯泡不回报状态，实体是"HA 最后一次下发"的假定值（已持久化）
- 本型号灯泡不广播，自动发现对它无效，需要手动填 `light_id`