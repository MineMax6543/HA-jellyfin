"""Media browsing for every Jellyfin library type."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.media_player import BrowseError, BrowseMedia, MediaClass

from .api import JellyfinClient

ROOT = "root"
RESUME = "resume"
NEXT_UP = "nextup"
MAX_CHILDREN = 1000

_TYPE_CLASS = {
    "Movie": MediaClass.MOVIE,
    "Series": MediaClass.TV_SHOW,
    "Season": MediaClass.SEASON,
    "Episode": MediaClass.EPISODE,
    "MusicAlbum": MediaClass.ALBUM,
    "MusicArtist": MediaClass.ARTIST,
    "Audio": MediaClass.TRACK,
    "AudioBook": MediaClass.TRACK,
    "Playlist": MediaClass.PLAYLIST,
    "MusicVideo": MediaClass.VIDEO,
    "Video": MediaClass.VIDEO,
    "Trailer": MediaClass.VIDEO,
    "TvChannel": MediaClass.CHANNEL,
    "LiveTvChannel": MediaClass.CHANNEL,
    "Photo": MediaClass.IMAGE,
}
# Containers Jellyfin clients can play as a whole (queue of their children)
_PLAYABLE_CONTAINERS = {"MusicAlbum", "Playlist", "Season", "BoxSet", "MusicArtist"}

ThumbFn = Callable[[str, str], str]  # (item_id, image_tag) -> url


def _title(item: dict[str, Any]) -> str:
    name = item.get("Name") or "?"
    if item.get("Type") == "Episode":
        series = item.get("SeriesName")
        season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f"S{season:02d}E{episode:02d} " if isinstance(season, int) and isinstance(episode, int) else ""
        return f"{series} – {code}{name}" if series else f"{code}{name}"
    if item.get("Type") == "Movie" and item.get("ProductionYear"):
        return f"{name} ({item['ProductionYear']})"
    return name


def _thumbnail(item: dict[str, Any], thumb: ThumbFn) -> str | None:
    tag = (item.get("ImageTags") or {}).get("Primary")
    if tag:
        return thumb(item["Id"], tag)
    if item.get("SeriesId") and item.get("SeriesPrimaryImageTag"):
        return thumb(item["SeriesId"], item["SeriesPrimaryImageTag"])
    if item.get("AlbumId") and item.get("AlbumPrimaryImageTag"):
        return thumb(item["AlbumId"], item["AlbumPrimaryImageTag"])
    return None


def is_playable(item: dict[str, Any]) -> bool:
    return item.get("MediaType") in ("Video", "Audio") or item.get("Type") in _PLAYABLE_CONTAINERS


def item_to_browse(item: dict[str, Any], thumb: ThumbFn, children=None) -> BrowseMedia:
    item_type = item.get("Type") or "Folder"
    is_folder = bool(item.get("IsFolder"))
    return BrowseMedia(
        media_class=_TYPE_CLASS.get(item_type, MediaClass.DIRECTORY if is_folder else MediaClass.VIDEO),
        media_content_id=item["Id"],
        media_content_type=item_type,
        title=_title(item),
        can_play=is_playable(item),
        can_expand=is_folder,
        children=children,
        thumbnail=_thumbnail(item, thumb),
    )


def _folder(content_id: str, title: str, children: list[BrowseMedia] | None) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type="library",
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.DIRECTORY,
    )


async def async_browse(
    client: JellyfinClient, content_id: str | None, thumb: ThumbFn
) -> BrowseMedia:
    """Browse the library tree; None or 'root' is the top level."""
    if content_id in (None, "", ROOT):
        views = await client.user_views()
        children = [
            _folder(RESUME, "Continue watching", None),
            _folder(NEXT_UP, "Next up", None),
            *(item_to_browse(view, thumb) for view in views),
        ]
        return _folder(ROOT, "Jellyfin", children)
    if content_id == RESUME:
        items = await client.resume_items()
        return _folder(RESUME, "Continue watching", [item_to_browse(i, thumb) for i in items])
    if content_id == NEXT_UP:
        items = await client.next_up()
        return _folder(NEXT_UP, "Next up", [item_to_browse(i, thumb) for i in items])

    item = await client.item(content_id)
    if item is None:
        raise BrowseError(f"Jellyfin item {content_id} not found")
    children: list[BrowseMedia] | None = None
    if item.get("IsFolder"):
        items = await client.items(
            parentId=content_id,
            sortBy="IsFolder,ParentIndexNumber,IndexNumber,SortName",
            limit=MAX_CHILDREN,
            fields="PrimaryImageAspectRatio",
        )
        children = [item_to_browse(i, thumb) for i in items]
    return item_to_browse(item, thumb, children=children)


async def async_search(
    client: JellyfinClient, query: str, thumb: ThumbFn, limit: int = 50
) -> list[BrowseMedia]:
    items = await client.items(
        searchTerm=query,
        recursive=True,
        limit=limit,
        includeItemTypes="Movie,Series,Episode,Audio,MusicAlbum,MusicArtist,Playlist,MusicVideo",
    )
    return [item_to_browse(i, thumb) for i in items]
