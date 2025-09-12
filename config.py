# =========================
# T-Watch S3 · config.py
# =========================

# ---- Navigation ----
START_SCREEN = "clock_digital"

# ---- Touch / Input ----
TOUCH_SWAP_XY  = False
TOUCH_FLIP_X   = False
TOUCH_FLIP_Y   = False

# ---- Display ----
DISPLAY_HZ        = 80_000_000  # nur wenn vom Displaytreiber genutzt
BACKLIGHT_DIM     = 0           # 0..255, Helligkeit im DIM-Zustand
BACKLIGHT_BRIGHT  = 200         # 0..255, Aktuelle Helligkeit

# ---- Power / Sleep ----
SLEEP_MODE        = "dim"       # "off" | "dim" | "lightsleep" | "deepsleep"
DIM_TIMEOUT_MS    = 50_000      # bis DIM
SLEEP_TIMEOUT_MS  = 90_000      # bis Sleep (nur bei light/deep)
BATTERY_UPDATE_MS = 10_000      # Interval in ms zum Überprüfen des Batteriestands

# Vorwarnungen (Statusbar/Overlay), 0 = aus
PRE_DIM_NOTICE_MS   = 2_000
PRE_SLEEP_NOTICE_MS = 5_000

# Wake-Sources
WAKEUP_TOUCH = True

# ---- Funk/Audio-Policy (derzeit nur Flags; Treiber optional anbinden) ----
SLEEP_WIFI  = False
SLEEP_BLE   = False
SLEEP_LORA  = True
SLEEP_AUDIO = True

# ---- Debug ----
LOG_LEVEL       = 20
DEBUG_TOUCH     = False
DEBUG_NAV       = False
DEBUG_SM        = False
DEBUG_PM        = False
WRAP_VCHAIN     = False  # True = am Rand der vchain wrapen
DEBUG_SETTINGS  = False
DEBUG_ASSETS    = False
DEBUG_WIFI      = False
DEBUG_BLE       = False
DEBUG_LORA		= False

# --- Look & Behavior ---
TIME_24H     = True
SHOW_SECONDS = True

# ---- Watchfaces
ACTIVE_WATCHFACE_DIGITAL = "gold_waves_orbitron"
ACTIVE_WATCHFACE_ANALOG  = "gold_classic_analog"
ACTIVE_THEME             = "black_basic"

# ---- LoRa ----
LORA_ENABLED           = True        # Master-Schalter
LORA_PAUSE_ON_DIM      = False         # DIM: Radio an, TX pausiert (wenn SLEEP_LORA=False)
# Status/TTL-Policy (StatusStore)
LORA_STATUS_TTL_MS        = 8_000
LORA_STATUS_RATE_LIMIT_MS = 500
LORA_STATUS_COALESCE_MS   = 16
LORA_DIAG_RATE_LIMIT_MS   = 250
# Duty/Backoff
LORA_TX_COOLDOWN_MS    = 0            # 0 = aus; sonst Pause nach TX
LORA_BACKOFF_MAX_MS    = 60_000

# --- Wifi Configuration ---
WIFI_ENABLED  = False
WIFI_SSID     = "PN"
WIFI_PASS     = "5667361407102283a!"
USE_DHCP      = True
STATIC_IP     = "192.168.178.101"
NETMASK       = "255.255.255.0"
GATEWAY       = "192.168.178.1"
DNS           = "192.168.178.1"
WIFI_RSSI_PUBLISH_INTERVAL_MS = 15_000
WIFI_CONNECTED_TTL_MS         = 60_000
WIFI_CONNECT_TIMEOUT_MS       = 12_000
WIFI_DHCP_TIMEOUT_MS          = 8_000
WIFI_BACKOFF_MAX_MS           = 60_000
WIFI_AP_FALLBACK              = False

# (Optional, empfohlen – StatusStore-Policy für Wi-Fi analog zu BT)
WIFI_STATUS_TTL_MS        = 8_000   # TTL für on/connecting/error
WIFI_STATUS_RATE_LIMIT_MS = 500     # min. Abstand status/wifi Publishes

# ---- BLE / BT ----
BLE_ENABLED         = False         # Master-Schalter
BLE_BRIDGE_ENABLED  = True
BLE_PAIRING_ON_START= False

BLE_DEVICE_NAME     = "TWatchS3"
BLE_SERVICE_UUID    = "12345678-1234-5678-9abc-def012345678"
BLE_CHAR_RX_UUID    = "12345678-1234-5678-9abc-def012345679"  # Phone -> Watch (WriteNR)
BLE_CHAR_TX_UUID    = "12345678-1234-5678-9abc-def01234567a"  # Watch -> Phone (Notify)

BLE_CONNECT_TIMEOUT_MS = 12_000
BLE_BACKOFF_MAX_MS     = 60_000

# --- Neu: Status/TTL-Policy (API v1.2 / StatusStore) ---
BLE_STATUS_TTL_MS        = 8_000     # TTL für on/pairing/off/error
BLE_STATUS_RATE_LIMIT_MS = 500       # min. Abstand status/bt Publishes
BLE_STATUS_COALESCE_MS   = 32        # Coalescing-Fenster (ein Display-Frame)

# --- Neu: Connected "sticky" bzw. lange TTL ---
BLE_STATUS_CONNECTED_STICKY = True
BLE_STATUS_CONNECTED_TTL_MS = 300_000  # nur wirksam, wenn STICKY=False

# --- Neu: Pairing-Timeout (ein Publish auf 'on' + diag/bt:pairing_timeout) ---
BLE_PAIRING_TIMEOUT_MS = 30_000

# Advertising & Bridge
BLE_ADV_INTERVAL_MS        = 300         # ms (nur einmal definieren)
BLE_BRIDGE_CODEC           = "json"
BLE_BRIDGE_MTU             = 185
BLE_BRIDGE_ALLOW_IN        = ["*"]              # Phone -> Watch
BLE_BRIDGE_ALLOW_OUT       = ["status/notifications", "status/battery"]  # Watch -> Phone (enger, stromsparender)

# ---- Notifications (StatusStore-Policy) ----
NOTIF_TTL_MS        = 15_000   # wie lange „neu“ sichtbar bleibt
NOTIF_RATE_LIMIT_MS = 300      # min. Abstand zwischen Publishes

# ---- Nachrichten-Indikator (Briefsymbol) ----
MQTT_MESSAGE        = True   # MQTT msg setzt Briefsymbol mit
NOTIF_CLEAR_ON_OPEN = True   # Messages-Screen publisht notif/clear_indicator beim Öffnen
