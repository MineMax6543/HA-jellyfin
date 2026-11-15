"""Constants for Jellyfin Plus."""

from __future__ import annotations

DOMAIN = "jellyfin_plus"
VERSION = "0.2.0"
CLIENT_NAME = "Jellyfin Plus"
DEVICE_NAME = "Home Assistant"

CONF_DEVICE_ID = "device_id"
CONF_TOKEN = "token"
CONF_USER_ID = "user_id"
CONF_SERVER_ID = "server_id"
CONF_IS_ADMIN = "is_admin"

# Fired on the HA bus for every playback change (for YAML automations)
EVENT_PLAYBACK = f"{DOMAIN}_playback"

ACTION_STARTED = "playback_started"
ACTION_PAUSED = "playback_paused"
ACTION_RESUMED = "playback_resumed"
ACTION_STOPPED = "playback_stopped"
ACTIONS = [ACTION_STARTED, ACTION_PAUSED, ACTION_RESUMED, ACTION_STOPPED]

# Jellyfin counts time in ticks of 100 ns
TICKS_PER_SECOND = 10_000_000

RECONNECT_MIN_SECONDS = 5
RECONNECT_MAX_SECONDS = 300
DEFAULT_KEEPALIVE_SECONDS = 30

SIGNAL_PLAYBACK = f"{DOMAIN}_playback_{{}}"
SIGNAL_CONNECTION = f"{DOMAIN}_connection_{{}}"
SIGNAL_SESSIONS = f"{DOMAIN}_sessions_{{}}"
