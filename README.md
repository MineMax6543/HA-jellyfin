# Jellyfin Plus

Instant Jellyfin playback events for Home Assistant: **started, paused, resumed and
stopped**, pushed by the Jellyfin server over a WebSocket the moment they happen (no
polling). Use them to dim the lights when a movie starts, bring them back up on pause, or
send a notification when someone starts watching.

Runs alongside Home Assistant's built-in Jellyfin integration.

## What you get

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
