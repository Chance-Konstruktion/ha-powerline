"""Localized text for persistent topology-alert notifications."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_TRANSLATION_DIR = Path(__file__).parent / "notification_translations"


@lru_cache(maxsize=2)
def _catalog(language: str) -> dict[str, dict[str, str]]:
    """Load one small, packaged notification catalog and cache it."""
    with (_TRANSLATION_DIR / f"{language}.json").open(encoding="utf-8") as stream:
        return json.load(stream)


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
