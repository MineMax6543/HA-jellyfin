# Jellyfin Plus reference

Everything in detail. For a quick start see the [README](../README.md).

- [Login](#login)
- [Media player](#media-player)
- [Remote](#remote)
- [Events](#events)
- [Sensors and calendar](#sensors-and-calendar)
- [Server entities](#server-entities)
- [Actions](#actions)
- [Several people](#several-people)
- [Troubleshooting](#troubleshooting)

## Login

| | API key | Username & password |
|---|---|---|
| Create | Jellyfin *Dashboard → API Keys* | an existing Jellyfin account |
| Sees | every user's playback and data | its own playback; everyone's if administrator |
| Controls | every app | its own apps; others with *Allow remote control of other users* (Jellyfin 12+) |
| Server features | always | administrators only |
| Stored | the key | an access token (never the password) |

With an API key you pick a **library user**: whose libraries, *Next up* and counts are
shown by default. Change it under *Configure*.

Admin rights of a password login are checked at startup and every 5 minutes; granting or
removing them in Jellyfin needs no re-setup. If Jellyfin stops accepting the login or key,
Home Assistant asks for a new one.

## Media player

One `media_player` per Jellyfin app that supports remote control, created the first time
it connects and kept from then on (tied to the device, not the session).

| State | Meaning |
|---|---|
| `off` | app not connected |
| `idle` | connected, nothing playing |
| `playing` / `paused` | |
| `unavailable` | Jellyfin server not reachable |

- **Controls:** play, pause, stop, seek, next/previous; volume and mute if the app supports
  them. The new state shows immediately and is confirmed by the app (max. 8 s).
- **Play media:** plays a Jellyfin item and resumes where you left off; *add* / *play next*
  queue it instead.
- **Browse / search:** every library, with *Continue watching* and *Next up* at the top.
- **Resume protection:** if an app answers "continue" by starting the item over, or by
  jumping to another item of the queue (Jellyfin Web does both), Jellyfin Plus goes back to
  where you paused and logs a warning. Next, previous, seek and stop are never undone.
- **Audio track / Subtitles:** two selects per app (if it supports switching tracks), available
  while something is playing. Picking an option switches the track on the TV.
- **Message on the screen:** `jellyfin_plus.send_message` (see [Actions](#actions)).

## Remote

`remote.send_command` with Jellyfin command names (`MoveUp`, `Select`, `Back`, `GoHome`, …)
or the short forms `up`, `down`, `left`, `right`, `select`, `back`, `home`, `menu`,
`volume_up`, `volume_down`, `mute`. The attribute `supported_commands` lists what the app
understands. Turning the remote **off** stops playback.

```yaml
action: remote.send_command
target:
  entity_id: remote.living_room_apple_tv_remote
data:
  command: [down, down, select]
```

## Events

| Entity / bus event | Fires |
|---|---|
| `event.<app>_playback` | playback of that app |
| `event.<server>_playback_all_clients` | playback of any app |
| bus `jellyfin_plus_playback` | the same, for YAML (`action` instead of `event_type`) |
| `event.<server>_library`, bus `jellyfin_plus_library` | `item_added`: new movie, episode, series, album, video |
| `event.<server>_activity`, bus `jellyfin_plus_activity` | Jellyfin's activity log (administrators) |

**Playback event types:** `playback_started`, `playback_paused`, `playback_resumed`,
`playback_stopped`, and with a segment plugin (e.g. *Intro Skipper*) `intro_started` /
`intro_ended`, `credits_*`, `recap_*`, `preview_*`, `commercial_*`. The next episode gives
`playback_stopped` (old) then `playback_started` (new). Credits are timed by Jellyfin Plus
itself, so they fire when the credits begin, not at the app's next report.

**Playback event data:**

| Field | Example |
|---|---|
| `device_name`, `device_id`, `client` | `Living Room Apple TV`, …, `Swiftfin` |
| `user_name`, `user_id`, `ha_user_id` | `max`, …, the linked Home Assistant user |
| `title`, `item_type`, `media_type` | `Dune`, `Movie`, `Video` |
| `series_name`, `season_number`, `episode_number` | `Severance`, `1`, `2` |
| `year`, `runtime`, `position` (seconds) | `2021`, `9360`, `60` |
| `is_paused`, `play_method`, `is_transcoding` | `false`, `DirectPlay`, `false` |
| `segment`, `segment_start`, `segment_end` | segment events only |

**Library event data:** `title`, `item_type`, `series_name`, `season_number`,
`episode_number`, `year`, `image_url` (poster on your Jellyfin server).

**Activity event types:** `login_failed`, `login_succeeded`, `user_locked_out`,
`task_failed`, `plugin_installed`, `plugin_updated`, `plugin_uninstalled`,
`plugin_install_failed`, `user_created`, `user_deleted`, `user_password_changed`, `other`.
Session and playback entries only go to the bus event.

## Sensors and calendar

| Entity | |
|---|---|
| *Next up* | episodes in Jellyfin's Next up, list in `episodes` |
| *Movies*, *Series*, *Episodes*, *Songs*, *Albums*, *Artists* | library counts (*Music videos*, *Collections*, *Books* if present) |
| *Recently added* | newest movie/episode/album; the newest 15 in `items` (title, `item_id`, `image_url`, `added`). Refreshes as soon as something is added |
| *Active streams* | playing apps, list in `streams` |
| *Transcoding streams*, *Streaming bandwidth* | of those streams |
| *Connected clients* | connected apps, list in `clients` |
| Calendar *Upcoming episodes* | all-day events on the air date |

Counts, Next up and the calendar refresh every 5 minutes; stream sensors update live.

## Server entities

Administrator login or API key only.

| Entity | |
|---|---|
| *Running tasks* | scheduled tasks with progress, live |
| *… free space* | per library and for Jellyfin's data, `used_percent` attribute |
| Update *Server* | a newer Jellyfin release exists (checked against GitHub every 6 h; nothing about you is sent) |
| Update *… plugin* | per plugin from Jellyfin's catalog; *Install* updates it, then restart Jellyfin |
| Buttons | *Scan library*, *Create backup*, *Restart* |

## Actions

| Action | Fields |
|---|---|
| `jellyfin_plus.send_message` | target media player; `message`, `title`, `timeout` (s, 0 = until dismissed), `pause` |
| `jellyfin_plus.sleep_timer` | target media player; `minutes` **or** `end_of_item: true`; `action` `pause` (default) / `stop`. Shown in the attributes `sleep_timer`, `sleep_timer_ends`. `cancel_sleep_timer` cancels it. |
| `jellyfin_plus.mark_played` / `mark_unplayed` | `item_id`; optional `jellyfin_user`, `config_entry_id` |
| `jellyfin_plus.set_favorite` | `item_id`, `favorite` (default on); optional `jellyfin_user`, `config_entry_id` |

`item_id` is in every playback and library event.

## Several people

*Jellyfin Plus → Configure* links Home Assistant users to Jellyfin users. Jellyfin always
saves progress for the user logged in on the playing app; the links decide whose data
Jellyfin Plus reads and acts for:

![Whose progress is used](images/whose-progress.svg)

- **Play** resumes at that person's position.
- **Per person:** a *Jellyfin &lt;name&gt;* device with *Next up*, *Currently watching* and
  *Upcoming episodes*.
- **Options:** a *People* folder in the media browser; adding the person to a shared app's
  session so Jellyfin saves the progress for them too.

Needs an API key or an administrator login.

## Troubleshooting

- **Download diagnostics** (*Jellyfin Plus → ⋮*): current sessions, each media player's
  internal state and the last 300 updates, commands and position decisions. Works without
  debug logging; tokens, IPs and user names are removed.
- **Debug logging** (*⋮ → Enable debug logging*): every command (`-> Pause`) and every
  update with shown, reported and expected position and why it was or wasn't corrected.
- **Server unreachable:** entities become unavailable; Jellyfin Plus reconnects on its own
  (5 s, backing off to 5 min).
- **Progress bar runs ahead while playing, correct when paused:** the clock of the device
  showing the dashboard is off. Sync its time.
- **Commands fail with 403 (Jellyfin 12):** use an API key, or enable *Allow remote control
  of other users* for the login.
