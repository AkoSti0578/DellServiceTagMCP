"""Offline helpers for Dell service tags (no API access needed)."""

from __future__ import annotations

import re
from collections.abc import Iterable

# A Dell service tag is 7 alphanumeric characters (older systems: 5).
_SERVICE_TAG_RE = re.compile(r"^[A-Z0-9]{5}$|^[A-Z0-9]{7}$")
_SPLIT_RE = re.compile(r"[\s,;]+")


def normalize_service_tags(raw: Iterable[str] | str) -> list[str]:
    """Split, trim, upper-case and de-duplicate service tags while keeping order.

    Accepts a single string ("ABC1234, DEF5678") or a list of strings, which may
    themselves contain separators (comma, semicolon, whitespace).
    """
    if isinstance(raw, str):
        raw = [raw]
    seen: dict[str, None] = {}
    for item in raw:
        for part in _SPLIT_RE.split(item or ""):
            tag = part.strip().upper()
            if tag:
                seen.setdefault(tag, None)
    return list(seen)


def is_valid_service_tag(tag: str) -> bool:
    return bool(_SERVICE_TAG_RE.match(tag.strip().upper()))


def express_service_code(tag: str) -> str:
    """Dell Express Service Code: the service tag interpreted as a base-36 number."""
    return str(int(tag.strip().upper(), 36))


def support_url(tag: str) -> str:
    return (
        "https://www.dell.com/support/home/en-us/product-support/servicetag/"
        f"{tag.strip().upper()}/overview"
    )


def describe_offline(tag: str) -> dict:
    tag = tag.strip().upper()
    valid = is_valid_service_tag(tag)
    return {
        "serviceTag": tag,
        "validFormat": valid,
        "expressServiceCode": express_service_code(tag) if valid else None,
        "supportUrl": support_url(tag) if valid else None,
    }
