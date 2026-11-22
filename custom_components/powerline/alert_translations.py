"""Localized text for persistent topology-alert notifications."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

_TRANSLATION_DIR = Path(__file__).parent / "notification_translations"

# Every locale alert_notification_text can resolve ends in one of these two
# catalogs, so preloading both covers every alert the coordinator can raise.
LOCALES = ("de", "en")


@lru_cache(maxsize=2)
def _catalog(language: str) -> dict[str, dict[str, str]]:
    """Load one small, packaged notification catalog and cache it."""
    with (_TRANSLATION_DIR / f"{language}.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def _preload_catalogs() -> None:
    """Read both packaged catalogs (executor side of async_preload_catalogs)."""
    for language in LOCALES:
        try:
            _catalog(language)
        except (OSError, ValueError):
            # A missing or corrupt catalog stays a per-alert problem instead
            # of failing setup; alert_notification_text still raises KeyError.
            _LOGGER.debug("Could not preload %s notification catalog", language)


async def async_preload_catalogs(hass: Any) -> None:
    """Warm the catalog cache off the event loop.

    The coordinator raises alerts from its poll callback, i.e. on the event
    loop: letting the first alert there open the catalog file would be
    blocking file I/O ("Detected blocking call to open"). Setup reads the two
    small packaged files once in the executor instead (#12).
    """
    await hass.async_add_executor_job(_preload_catalogs)


def alert_notification_text(
    language: str,
    key: str,
    **values: Any,
) -> dict[str, str]:
    """Format translated title/message text, falling back to English."""
    locale = (language or "en").replace("_", "-").split("-", 1)[0].lower()
    if locale not in {"de", "en"}:
        locale = "en"

    catalog = _catalog(locale)
    entry = catalog.get(key)
    if entry is None and locale != "en":
        entry = _catalog("en").get(key)
    if entry is None:
        raise KeyError(f"Unknown Powerline notification key: {key}")

    return {
        field: template.format(**values)
        for field, template in entry.items()
    }
