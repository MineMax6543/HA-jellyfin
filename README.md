<img src="custom_components/jellyfin_plus/brand/icon.png" alt="Jellyfin Plus icon" width="96" align="right">

# Jellyfin Plus

A more reliable Jellyfin integration for Home Assistant. Everything is pushed by the
Jellyfin server over a WebSocket the moment it happens (no polling), and every entity is
tied to the client **device**, so it stays the same when an app restarts.

- **Media player per client**: what's playing, poster, progress, play/pause/stop/seek,
  next/previous, volume, play media with resume, browse and search every library.
- **Remote per client**: navigation commands (up/down/left/right, select, back, home, …).
- **Playback events**: started, paused, resumed, stopped, for automations.

Can replace Home Assistant's built-in Jellyfin integration, or run alongside it.

## Why not the built-in integration?

| | Built-in | Jellyfin Plus |
|---|---|---|
| Updates | polls every 10 s | pushed instantly |
| Entity tied to | the session (new entity whenever an app restarts, old one stays unavailable) | the device (always the same entity) |
| App closed | `unavailable` | `off` |
| Playback events | – | ✅ |
| Browse | music, movies, TV | every library, plus *Continue watching* and *Next up* |
| Play media | starts from the beginning | resumes where you left off |

## Media player

One `media_player` per client that supports remote control (Jellyfin apps on Apple TV,
iPhone, Android TV, the web app, …), created the first time the client connects and kept
from then on.

- **States**: `off` (app not connected), `idle` (connected, nothing playing), `playing`,
  `paused`. Only a lost connection to the server makes it `unavailable`.
- **Controls**: play, pause, stop, seek, next/previous, plus volume and mute when the
  client supports them.
- **Play media**: plays a Jellyfin item on that client and resumes where you left off;
  *add* / *play next* queue instead.
- **Browse / search**: every library, with *Continue watching* and *Next up* at the top.

## Remote

`remote.send_command` with Jellyfin command names (`MoveUp`, `Select`, `Back`, `GoHome`,
…) or the short forms `up`, `down`, `left`, `right`, `select`, `back`, `home`, `menu`.
The entity lists what the client supports in its `supported_commands` attribute.

```yaml
action: remote.send_command
target:
  entity_id: remote.living_room_apple_tv_remote
data:
  command: [down, down, select]
```

## Playback events

- **An event entity per Jellyfin client** (e.g. *Living Room Apple TV Playback*). It is
  created the first time that client plays something and survives restarts.
- **An "all clients" event entity** on the server device.
- **A bus event `jellyfin_plus_playback`** for YAML automations.

Every event carries:

| Field | Example |
|---|---|
| `action` (bus event) / `event_type` (entity) | `playback_started`, `playback_paused`, `playback_resumed`, `playback_stopped` |
| `device_name`, `device_id`, `client` | `Living Room Apple TV`, …, `Swiftfin` |
| `user_name`, `user_id` | `max` |
| `title`, `item_type`, `media_type` | `Dune`, `Movie`, `Video` |
| `series_name`, `season_number`, `episode_number` | `Severance`, `1`, `2` (episodes) |
| `year`, `runtime`, `position` (seconds) | `2021`, `9360`, `60` |
| `is_paused`, `play_method`, `is_transcoding` | `false`, `DirectPlay`, `false` |

Switching to the next episode produces `playback_stopped` (old episode) followed by
`playback_started` (new one).

## Setup

1. Copy `custom_components/jellyfin_plus` to `config/custom_components/` and restart.
2. **Settings → Devices & services → Add integration → Jellyfin Plus**.
3. Enter the server URL (e.g. `http://192.168.1.10:8096`), username and password.
   Use an **administrator** account to get events for every user; a normal account only
   sees its own playback. Only an access token is stored, not the password.

## Example: movie mode

```yaml
automation:
  - alias: Movie mode
    triggers:
      - trigger: state
        entity_id: event.living_room_apple_tv_playback
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.item_type in ['Movie', 'Episode'] }}"
    actions:
      - choose:
          - conditions: "{{ trigger.to_state.attributes.event_type in ['playback_started', 'playback_resumed'] }}"
            sequence:
              - action: light.turn_off
                target: { area_id: living_room }
          - conditions: "{{ trigger.to_state.attributes.event_type in ['playback_paused', 'playback_stopped'] }}"
            sequence:
              - action: light.turn_on
                target: { area_id: living_room }
                data: { brightness_pct: 40 }
```

Or with the bus event:

```yaml
triggers:
  - trigger: event
    event_type: jellyfin_plus_playback
    event_data:
      action: playback_started
      device_name: Living Room Apple TV
```

## Notes

- If the server goes away, the entities become unavailable and the integration reconnects
  on its own (5 s, then backing off up to 5 min).
- If Jellyfin stops accepting the saved login, Home Assistant asks you to log in again.
