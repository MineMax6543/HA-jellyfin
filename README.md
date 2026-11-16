<img src="custom_components/jellyfin_plus/brand/icon.png" alt="Jellyfin Plus icon" width="96" align="right">

# Jellyfin Plus

A more reliable Jellyfin integration for Home Assistant. Everything is pushed by the
Jellyfin server over a WebSocket the moment it happens (no polling), and every entity is
tied to the client **device**, so it stays the same when an app restarts.

- **Media player per client**: what's playing, poster, progress, play/pause/stop/seek,
  next/previous, volume, play media with resume, browse and search every library.
- **Remote per client**: navigation commands (up/down/left/right, select, back, home, …).
- **Playback events**: started, paused, resumed, stopped, plus **intro / credits / recap**
  segments, for automations.
- **Messages on the TV**: show a message over what's playing (e.g. the doorbell).
- **Library events**: a new movie or episode was added, with poster.
- **Upcoming episodes calendar** and a **Next up** sensor.
- **Server monitoring** (admin): streams, transcodes, bandwidth, free space, running tasks,
  update entities for Jellyfin and its plugins, buttons for library scan / backup / restart,
  and the activity log (e.g. failed logins).

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

Controls react instantly in Home Assistant: after a successful command the expected state
is shown right away and corrected by the app's next report (or after 8 s if the app
never confirms). Some apps restart the item when asked to resume; Jellyfin Plus notices
this and seeks back to where you paused, logging a warning that names the player.

**Troubleshooting**:
- *Settings → Devices & services → Jellyfin Plus → ⋮ → Download diagnostics* works at any
  time, no debug logging needed: it contains the current sessions, each media player's
  internal state and the last 300 received updates, sent commands and position decisions
  (tokens, IPs and user names are removed). Download it right after something looked
  wrong.
- *⋮ → Enable debug logging* additionally logs every command (`-> Pause`) and every update
  with the shown, reported and expected position and why it was (not) corrected.

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

## Intro and credits events

The playback event entities also fire `intro_started` / `intro_ended`,
`credits_started` / `credits_ended`, `recap_*`, `preview_*` and `commercial_*`, with the
extra fields `segment`, `segment_start` and `segment_end` (seconds).

Jellyfin only knows these segments if a **segment provider plugin** is installed, for
example *Intro Skipper*. Without one, these events simply never fire. Between position
reports from the client, Jellyfin Plus keeps time itself, so `credits_started` fires when
the credits begin rather than at the next report.

```yaml
# Lights up slowly when the credits start
triggers:
  - trigger: state
    entity_id: event.living_room_apple_tv_playback
    attribute: event_type
    to: credits_started
actions:
  - action: light.turn_on
    target: { area_id: living_room }
    data: { brightness_pct: 60, transition: 20 }
```

## Message on the TV

```yaml
action: jellyfin_plus.send_message
target:
  entity_id: media_player.living_room_apple_tv
data:
  title: Front door
  message: Someone is at the door
  timeout: 10     # seconds, 0 = until dismissed
  pause: true     # pause playback first
```

## Library: new items

The **Library** event entity (on the server device) and the bus event
`jellyfin_plus_library` fire `item_added` for new movies, episodes, series, albums and
videos, with `title`, `item_type`, `series_name`, `season_number`, `episode_number`,
`year` and `image_url` (the poster on your Jellyfin server).

## Upcoming episodes and Next up

- **Calendar "Upcoming episodes"**: all-day events on the air date of upcoming episodes of
  the shows in your library.
- **Sensor "Next up"**: number of episodes in Jellyfin's *Next up*, the list is in the
  `episodes` attribute.
- **Library counts**: *Movies*, *Series*, *Episodes*, *Songs*, *Albums*, *Artists*, plus
  *Music videos*, *Collections* and *Books* if your library has any (as seen by the
  configured user, refreshed every 5 minutes).

## Server (administrator account)

| Entity | What |
|---|---|
| Sensor *Active streams* | playing clients, list in `streams` |
| Sensor *Transcoding streams* | how many of them are transcoded |
| Sensor *Connected clients* | connected apps, list in `clients` |
| Sensor *Streaming bandwidth* | best effort: transcode bitrate, else the source bitrate |
| Sensor *Running tasks* | running scheduled tasks with progress (pushed live) |
| Sensor *… free space* | per library and for Jellyfin's own data, `used_percent` attribute |
| Update *Server* | newer Jellyfin release available (install it the way you installed Jellyfin) |
| Update *… plugin* | per plugin from Jellyfin's catalog; *Install* updates it (restart Jellyfin afterwards) |
| Buttons | *Scan library*, *Create backup*, *Restart* |
| Event *Activity* | Jellyfin's activity log: `login_failed`, `user_locked_out`, `task_failed`, `plugin_updated`, … (bus event `jellyfin_plus_activity` gets every entry) |

With a normal account these are left out (Jellyfin doesn't allow them), everything else
works.

The server update entity asks GitHub for the latest Jellyfin release every 6 hours
(`api.github.com`, no data about you or your server is sent).

## Multiple users

Link Home Assistant users to their Jellyfin accounts under *Jellyfin Plus → Configure*.
Jellyfin always saves progress for the user logged in on the playing device; the links
decide whose data Jellyfin Plus *reads and acts for*:

1. a person picked in the *People* folder or given as `jellyfin_user`,
2. else the linked Home Assistant user who started it,
3. else the Jellyfin user logged in on the device (also the default for browsing),
4. else the integration's own account.

- **Play** resumes at that person's position. Optional: also add the person to a shared
  device's session, so Jellyfin saves the progress for them too.
- **Per person**: *Next up* and *Currently watching* sensors and an *Upcoming episodes*
  calendar, on a "Jellyfin &lt;name&gt;" device.
- **Actions** `jellyfin_plus.mark_played`, `mark_unplayed`, `set_favorite`.
- Playback events carry `ha_user_id`.
- Optional *People* folder in the media browser with each person's *Continue watching*
  and *Next up*.

Needs an administrator account (to read other users' data).

## Setup

1. Copy `custom_components/jellyfin_plus` to `config/custom_components/` and restart.
2. **Settings → Devices & services → Add integration → Jellyfin Plus**.
3. Enter the server URL (e.g. `http://192.168.1.10:8096`), username and password.
   Use an **administrator** account to get events for every user; a normal account only
   sees its own playback. Only an access token is stored, not the password.
   **Jellyfin 12+**: to control apps that are logged in as *another* Jellyfin user, also
   enable *Allow remote control of other users* for this account (Dashboard → Users →
   Profile). Without it, commands fail with a message saying so.

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
