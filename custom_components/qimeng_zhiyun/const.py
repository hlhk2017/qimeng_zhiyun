"""常量定义。"""

DOMAIN = "qimeng_zhiyun"
NAME = "启明智云 FastCon 蓝牙灯"

# BLE 厂商数据 company id：Broadlink FastCon / brMesh
COMPANY_ID = 0xFFF0

CONF_MESH_KEY = "mesh_key"
CONF_TRANSMITTER_TYPE = "transmitter_type"
CONF_TRANSMITTER_ENTITY = "transmitter_entity"
CONF_TRANSMITTER_ENTITIES = "transmitter_entities"
CONF_API_TRANSMITTERS = "api_transmitters"   # [{"host": "192.168.2.21", "key": "<noise_psk>"}]
CONF_TRANSMITTER_SERVICE = "transmitter_service"
CONF_TRANSMITTER_DEVICE = "transmitter_device"
CONF_BLUEZ_ADAPTER = "bluez_adapter"
CONF_ADV_DURATION = "adv_duration"
CONF_REPEAT = "repeat_count"
CONF_LIGHTS = "lights"
CONF_LIGHT_STYLE = "light_style"

TRANSPORT_TEXT = "text"
TRANSPORT_SERVICE = "service"
TRANSPORT_BLUEZ = "bluez"
TRANSPORT_API = "api"

STYLE_STANDARD = "standard"
STYLE_APP = "app"
STYLE_LABELS = {
    STYLE_STANDARD: "标准 6 字节（esphome-fastcon 公开协议，已验证）",
    STYLE_APP: "App 7 字节（启明智云 App 反汇编）",
}
DEFAULT_STYLE = STYLE_STANDARD

TRANSPORT_LABELS = {
    TRANSPORT_TEXT: "ESPHome 发射器（text 实体，可多选）",
    TRANSPORT_TEXT: "ESPHome 发射器（text 实体）",
    TRANSPORT_SERVICE: "ESPHome 发射器（用户动作服务）",
    TRANSPORT_API: "ESPHome 设备（直连 API，最稳）",
    TRANSPORT_BLUEZ: "本机蓝牙适配器（BlueZ）",
}

DEFAULT_ADV_DURATION = 60
DEFAULT_REPEAT = 1  # 重复交给发射器固件做，HA 侧默认发一次

DEFAULT_SERVICE = "esphome.fastcon_send"
DEFAULT_BLUEZ_ADAPTER = "hci0"

MANUFACTURER = "Broadlink (FastCon)"