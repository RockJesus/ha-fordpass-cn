"""常量定义 for FordPass CN."""

DOMAIN = "fordpass_cn"
CONF_VEHICLE_TYPE = "vehicle_type"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_VEHICLE_TYPE = "ford"
COORDINATOR = "coordinator"
FORD_API = "ford_api"
VEHICLES = "vehicles"
VEHICLE_TYPE_FORD = "ford"
VEHICLE_TYPE_LINCOLN = "lincoln"
VEHICLE_TYPES = {
    VEHICLE_TYPE_FORD: "福特派",
    VEHICLE_TYPE_LINCOLN: "林肯之道",
}
SERVICE_REFRESH_VEHICLE = "refresh_vehicle"
SERVICE_CLEAR_TOKENS = "clear_tokens"
PLATFORMS = ["sensor", "binary_sensor", "switch", "lock", "device_tracker"]
