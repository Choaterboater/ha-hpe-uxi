"""Constants for the HPE UXI integration."""

DOMAIN = "hpe_uxi"

CONF_BASE_URL = "base_url"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_TENANT_ID = "tenant_id"
CONF_SCAN = "scan_seconds"  # options: seconds between sweeps

MANUFACTURER = "HPE UXI"

# The UXI API host (from HPE's published OpenAPI document) and its path prefix.
DEFAULT_BASE_URL = "https://api.capenetworks.com"
API_PATH = "/networking-uxi/v1alpha1"
# GreenLake issues the OAuth tokens (client credentials grant), as for Central.
TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"

# Account event entity: what it can fire.
EVENT_SENSOR_OFFLINE = "sensor_offline"
EVENT_SENSOR_ONLINE = "sensor_online"
EVENT_ISSUE_CONFIRMED = "issue_confirmed"
EVENT_ISSUE_RESOLVED = "issue_resolved"
EVENT_TYPES = [EVENT_SENSOR_OFFLINE, EVENT_SENSOR_ONLINE, EVENT_ISSUE_CONFIRMED, EVENT_ISSUE_RESOLVED]

# Each sensor's status (online, testing, issues) costs one request; it is read every
# POLL_SECONDS by default, changeable in the options (MIN..MAX).
POLL_SECONDS = 60
MIN_POLL_SECONDS = 30
MAX_POLL_SECONDS = 600
# Sensors, agents, service tests and networks change rarely: read at most this often.
INVENTORY_SECONDS = 300
# List attributes are capped at this many rows.
MAX_LIST_ROWS = 200
# Short UXI errors keep the last known state; only after this long without a good
# sweep (at least two sweeps) do the entities become unavailable.
UNAVAILABLE_AFTER_SECONDS = 300
# Backoff ceiling after 429 / 5xx answers.
MAX_BACKOFF_SECONDS = 300
# Every UXI request gives up after this long, and requests are spaced this far apart.
REQUEST_TIMEOUT_SECONDS = 20
MIN_REQUEST_GAP_SECONDS = 0.35
# UXI lists return at most 100 items a page.
PAGE_LIMIT = 100
