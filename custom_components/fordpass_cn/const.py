"""常量定义 for FordPass CN."""

DOMAIN = "fordpass_cn"

# 配置项
CONF_VEHICLE_TYPE = "vehicle_type"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_SCAN_INTERVAL = "scan_interval"

# 默认值
DEFAULT_SCAN_INTERVAL = 5  # 分钟
DEFAULT_VEHICLE_TYPE = "ford"

# 数据键
COORDINATOR = "coordinator"
FORD_API = "ford_api"
VEHICLES = "vehicles"

# 车辆类型
VEHICLE_TYPE_FORD = "ford"
VEHICLE_TYPE_LINCOLN = "lincoln"

VEHICLE_TYPES = {
    VEHICLE_TYPE_FORD: "福特派",
    VEHICLE_TYPE_LINCOLN: "林肯之道",
}

# 服务名
SERVICE_REFRESH_VEHICLE = "refresh_vehicle"
SERVICE_CLEAR_TOKENS = "clear_tokens"

# 平台
PLATFORMS = ["sensor", "binary_sensor", "switch", "lock", "device_tracker"]
