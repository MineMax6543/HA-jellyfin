"""Tests for the session diffing (no Home Assistant needed)."""

from __future__ import annotations

from typing import Any

from custom_components.jellyfin_plus.sessions import SessionTracker

MOVIE = {
    "Id": "m1",
    "Name": "Dune",
    "Type": "Movie",
    "MediaType": "Video",
    "ProductionYear": 2021,
    "RunTimeTicks": 9_360_000_000_0,  # 9360 s
}
EPISODE = {
    "Id": "e1",
    "Name": "Pilot",
    "Type": "Episode",
    "SeriesName": "Severance",
    "ParentIndexNumber": 1,
    "IndexNumber": 1,
}


def session(
    device: str = "atv", item: dict | None = None, paused: bool = False, **extra: Any
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "DeviceId": device,
        "DeviceName": "Living Room Apple TV",
        "Client": "Swiftfin",
        "UserName": "max",
        "PlayState": {"IsPaused": paused, "PositionTicks": 600_000_000, "PlayMethod": "DirectPlay"},
        **extra,
    }
    if item is not None:
        data["NowPlayingItem"] = item
    return data


def actions(events) -> list[tuple[str, str]]:
    return [(e.action, e.device_id) for e in events]


def test_full_lifecycle() -> None:
    tracker = SessionTracker()
    assert tracker.update([session()]) == []  # idle client
    events = tracker.update([session(item=MOVIE)])
    assert actions(events) == [("playback_started", "atv")]
    data = events[0].data
    assert data["title"] == "Dune"
    assert data["item_type"] == "Movie"
    assert data["runtime"] == 9360
    assert data["position"] == 60
    assert data["is_transcoding"] is False

    assert actions(tracker.update([session(item=MOVIE, paused=True)])) == [
        ("playback_paused", "atv")
    ]
    assert tracker.update([session(item=MOVIE, paused=True)]) == []  # no change
    assert actions(tracker.update([session(item=MOVIE)])) == [("playback_resumed", "atv")]
    assert actions(tracker.update([session()])) == [("playback_stopped", "atv")]


def test_next_episode_is_stop_then_start() -> None:
    tracker = SessionTracker()
    tracker.update([session(item=EPISODE)])
    next_ep = {**EPISODE, "Id": "e2", "Name": "Half Loop", "IndexNumber": 2}
    events = tracker.update([session(item=next_ep)])
    assert actions(events) == [("playback_stopped", "atv"), ("playback_started", "atv")]
    assert events[0].data["title"] == "Pilot"
    assert events[1].data["episode_number"] == 2
    assert events[1].data["series_name"] == "Severance"


def test_client_disconnect_counts_as_stop_and_own_session_ignored() -> None:
    tracker = SessionTracker(own_device_id="ha")
    tracker.update([session(item=MOVIE), session(device="ha", item=EPISODE)])
    assert list(tracker.playing) == ["atv"]
    assert actions(tracker.update([])) == [("playback_stopped", "atv")]


def test_multiple_clients_and_transcoding() -> None:
    tracker = SessionTracker()
    events = tracker.update(
        [
            session(item=MOVIE),
            session(device="ipad", item=EPISODE, TranscodingInfo={"VideoCodec": "h264"}),
        ]
    )
    assert sorted(actions(events)) == [
        ("playback_started", "atv"),
        ("playback_started", "ipad"),
    ]
    ipad = next(e for e in events if e.device_id == "ipad")
    assert ipad.data["is_transcoding"] is True


def test_starting_paused_reports_both() -> None:
    tracker = SessionTracker()
    assert actions(tracker.update([session(item=MOVIE, paused=True)])) == [
        ("playback_started", "atv"),
        ("playback_paused", "atv"),
    ]
