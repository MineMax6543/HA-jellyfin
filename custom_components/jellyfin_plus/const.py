"""Constants for Jellyfin Plus."""

from __future__ import annotations

DOMAIN = "jellyfin_plus"
VERSION = "0.4.0"
CLIENT_NAME = "Jellyfin Plus"
DEVICE_NAME = "Home Assistant"

CONF_DEVICE_ID = "device_id"
CONF_TOKEN = "token"
CONF_USER_ID = "user_id"
CONF_SERVER_ID = "server_id"
CONF_IS_ADMIN = "is_admin"

# ---- bus events (for YAML automations) ---------------------------------------
EVENT_PLAYBACK = f"{DOMAIN}_playback"
EVENT_LIBRARY = f"{DOMAIN}_library"
EVENT_ACTIVITY = f"{DOMAIN}_activity"

# ---- playback actions -------------------------------------------------------
ACTION_STARTED = "playback_started"
ACTION_PAUSED = "playback_paused"
ACTION_RESUMED = "playback_resumed"
ACTION_STOPPED = "playback_stopped"

# Media segments (needs a segment provider plugin in Jellyfin, e.g. Intro Skipper).
# Jellyfin segment type -> our name
SEGMENT_NAMES = {
    "Intro": "intro",
    "Outro": "credits",
    "Recap": "recap",
    "Preview": "preview",
    "Commercial": "commercial",
}
SEGMENT_ACTIONS = [
    f"{name}_{edge}" for name in SEGMENT_NAMES.values() for edge in ("started", "ended")
]
ACTIONS = [ACTION_STARTED, ACTION_PAUSED, ACTION_RESUMED, ACTION_STOPPED, *SEGMENT_ACTIONS]

# ---- library ------------------------------------------------------------------
ACTION_ITEM_ADDED = "item_added"
# Item types announced as "added" (seasons, folders and single tracks are noise)
ANNOUNCED_ITEM_TYPES = {"Movie", "Episode", "Series", "MusicAlbum", "MusicVideo", "Video"}

# ---- activity log -------------------------------------------------------------
# Jellyfin activity Type -> event type on the Activity entity
ACTIVITY_TYPES = {
    "AuthenticationFailed": "login_failed",
    "AuthenticationSucceeded": "login_succeeded",
    "UserLockedOut": "user_locked_out",
    "TaskFailed": "task_failed",
    "PluginInstalled": "plugin_installed",
    "PluginUpdateInstalled": "plugin_updated",
    "PluginUninstalled": "plugin_uninstalled",
    "InstallationFailed": "plugin_install_failed",
    "UserCreated": "user_created",
    "UserDeleted": "user_deleted",
    "UserPasswordChanged": "user_password_changed",
}
ACTIVITY_OTHER = "other"
# Already covered by playback events; kept off the Activity entity (still on the bus)
ACTIVITY_IGNORED_ON_ENTITY = {
    "SessionStarted",
    "SessionEnded",
    "VideoPlayback",
    "VideoPlaybackStopped",
    "AudioPlayback",
    "AudioPlaybackStopped",
}

# Jellyfin counts time in ticks of 100 ns
TICKS_PER_SECOND = 10_000_000

RECONNECT_MIN_SECONDS = 5
RECONNECT_MAX_SECONDS = 300
DEFAULT_KEEPALIVE_SECONDS = 30
POLL_MINUTES = 5
GITHUB_LATEST_RELEASE = "https://api.github.com/repos/jellyfin/jellyfin/releases/latest"

SIGNAL_PLAYBACK = f"{DOMAIN}_playback_{{}}"
SIGNAL_CONNECTION = f"{DOMAIN}_connection_{{}}"
SIGNAL_SESSIONS = f"{DOMAIN}_sessions_{{}}"
SIGNAL_LIBRARY = f"{DOMAIN}_library_{{}}"
SIGNAL_ACTIVITY = f"{DOMAIN}_activity_{{}}"
SIGNAL_TASKS = f"{DOMAIN}_tasks_{{}}"
