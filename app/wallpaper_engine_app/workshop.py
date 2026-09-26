"""Read public Wallpaper Engine Workshop item details without a Steam API key."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib import error, parse, request


_DETAILS_URL = (
    "https://api.steampowered.com/"
    "ISteamRemoteStorage/GetPublishedFileDetails/v1/"
)
_APP_ID = "431960"
_ID_PATTERN = re.compile(r"[1-9][0-9]{0,23}\Z")
_BATCH_SIZE = 25
_MAX_IDS = 200
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_TIMEOUT_SECONDS = 10
_CACHE_SECONDS = 10 * 60
_CACHE_LIMIT = 512
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_cache_lock = threading.Lock()


def _steam_preview_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        url = parse.urlsplit(value)
        host = url.hostname
        port = url.port
    except ValueError:
        return None
    if (
        url.scheme.lower() != "https"
        or not host
        or url.username is not None
        or url.password is not None
        or port not in (None, 443)
    ):
        return None
    allowed = (
        host == "steamusercontent.com"
        or host.endswith(".steamusercontent.com")
        or host == "steamstatic.com"
        or host.endswith(".steamstatic.com")
        or host in {"steamcommunity-a.akamaihd.net", "steamuserimages-a.akamaihd.net"}
    )
    return value if allowed else None


def _item_type(tags: Any) -> str | None:
    if not isinstance(tags, list):
        return None
    for entry in tags:
        tag = entry.get("tag") if isinstance(entry, dict) else entry
        if isinstance(tag, str) and tag.casefold() in {"scene", "video"}:
            return tag.casefold()
    return None


def _query_batch(ids: list[str]) -> dict[str, dict[str, Any] | None]:
    fields = [("itemcount", str(len(ids)))]
    fields.extend((f"publishedfileids[{index}]", item_id) for index, item_id in enumerate(ids))
    body = parse.urlencode(fields).encode("ascii")
    outgoing = request.Request(
        _DETAILS_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "linux-wallpaperengine/1.0",
        },
        method="POST",
    )
    try:
        with request.urlopen(outgoing, timeout=_TIMEOUT_SECONDS) as response:
            final_url = parse.urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "api.steampowered.com":
                raise RuntimeError("A Steam redirecionou a consulta para um endereço inesperado.")
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        raise RuntimeError(f"A Steam recusou a consulta de wallpapers (HTTP {exc.code}).") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Não foi possível consultar o Workshop da Steam. Verifique a conexão.") from exc

    if len(payload) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("A resposta do Workshop excedeu o tamanho esperado.")
    try:
        data = json.loads(payload)
        envelope = data["response"]
        details = envelope["publishedfiledetails"]
    except (UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("A Steam devolveu uma resposta de wallpapers inválida.") from exc
    if not isinstance(envelope, dict) or envelope.get("result") != 1 or not isinstance(details, list):
        raise RuntimeError("A Steam não conseguiu consultar os detalhes dos wallpapers.")

    requested = set(ids)
    found: dict[str, dict[str, Any] | None] = {item_id: None for item_id in ids}
    for item in details:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("publishedfileid", ""))
        if (
            item_id not in requested
            or item.get("result") != 1
            or str(item.get("consumer_app_id")) != _APP_ID
        ):
            continue
        raw_title = item.get("title")
        title = raw_title.strip() if isinstance(raw_title, str) else ""
        result: dict[str, Any] = {
            "title": title or item_id,
            "preview_url": _steam_preview_url(item.get("preview_url")),
        }
        kind = _item_type(item.get("tags"))
        if kind is not None:
            result["type"] = kind
        found[item_id] = result
    return found


def fetch_details(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Return public metadata for known item IDs; omit unavailable or foreign items.

    Steam exposes this endpoint without a key. This function never signs in,
    subscribes to an item, or downloads Workshop content.
    """
    if not isinstance(ids, list):
        raise ValueError("Informe uma lista de IDs numéricos válidos do Workshop.")
    if len(ids) > _MAX_IDS:
        raise ValueError(f"Consulte no máximo {_MAX_IDS} wallpapers por vez.")
    if any(
        not isinstance(item_id, str) or not _ID_PATTERN.fullmatch(item_id)
        for item_id in ids
    ):
        raise ValueError("Informe uma lista de IDs numéricos válidos do Workshop.")
    unique = list(dict.fromkeys(ids))
    if not unique:
        return {}

    now = time.monotonic()
    with _cache_lock:
        pending = [
            item_id for item_id in unique
            if item_id not in _cache or _cache[item_id][0] <= now
        ]
    for offset in range(0, len(pending), _BATCH_SIZE):
        batch = pending[offset:offset + _BATCH_SIZE]
        queried = _query_batch(batch)
        expires = time.monotonic() + _CACHE_SECONDS
        with _cache_lock:
            for item_id, details in queried.items():
                _cache[item_id] = (expires, details)
            if len(_cache) > _CACHE_LIMIT:
                expired = [item_id for item_id, (deadline, _) in _cache.items() if deadline <= now]
                for item_id in expired:
                    _cache.pop(item_id, None)
            while len(_cache) > _CACHE_LIMIT:
                _cache.pop(next(iter(_cache)))

    with _cache_lock:
        return {
            item_id: dict(_cache[item_id][1])
            for item_id in unique
            if item_id in _cache and _cache[item_id][1] is not None
        }
