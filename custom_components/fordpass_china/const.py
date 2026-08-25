"""FordPass China 集成常量。"""

DOMAIN = "fordpass_china"
FORD_VEHICLES = "ford_vehicles"
ACCOUNTS = "accounts"
CONF_VEHICLE_TYPE = "vehicle_type"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_AUTH_TYPE = "auth_type"

# 默认轮询间隔（分钟）
DEFAULT_SCAN_INTERVAL = 5

# 支持的平台
PLATFORMS = ["device_tracker", "switch", "lock", "sensor", "binary_sensor"]
