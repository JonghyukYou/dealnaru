"""수집 데이터에서 발표에 부적절한 노골적 성인 콘텐츠를 판별합니다."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from html import unescape

# 폭력, 고어, 공포, 일반적인 성인 등급 표시는 의도적으로 포함하지 않습니다.
# 이 목록은 노골적인 성적 콘텐츠를 직접 나타내는 표현만 대상으로 합니다.
EXPLICIT_SEXUAL_CONTENT_MARKERS = (
    "adult only",
    "adults only",
    "adult game",
    "explicit nudity",
    "explicit sexual content",
    "strong sexual content",
    "pornographic",
    "pornography",
    "sex simulator",
    "sex simulation",
    "sex with",
    "sex game",
    "sex games",
    "porn",
    "lewd",
    "hentai",
    "eroge",
    "nsfw",
    "uncensored adult",
    "노골적인 성적",
    "야겜",
    "에로게",
    "음란",
    "포르노",
    "成人向け",
    "アダルトゲーム",
    "エロゲ",
)

NON_WORD_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
HTML_PATTERN = re.compile(r"<[^>]+>")
# 등급 안내에서 'no sexual content'를 부정적 판정으로 읽지 않습니다.
NEGATED_MARKERS = re.compile(
    r"\b(?:no|without|does not contain|not contain)\s+(?:explicit\s+)?"
    r"(?:sexual content|nudity|pornography)\b",
    re.IGNORECASE,
)


def iter_text_values(value):
    if value is None:
        return

    if isinstance(value, str):
        yield value
        return

    if isinstance(value, Mapping):
        for nested_value in value.values():
            yield from iter_text_values(nested_value)
        return

    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for nested_value in value:
            yield from iter_text_values(nested_value)


def normalize_content_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", unescape(HTML_PATTERN.sub(" ", value))).casefold()
    normalized = NEGATED_MARKERS.sub(" ", normalized)
    return NON_WORD_PATTERN.sub(" ", normalized).strip()


def has_explicit_sexual_content(*values) -> bool:
    normalized_markers = {
        normalize_content_text(marker) for marker in EXPLICIT_SEXUAL_CONTENT_MARKERS
    }

    for text in iter_text_values(values):
        normalized_text = normalize_content_text(text)
        padded_text = f" {normalized_text} "

        if any(f" {marker} " in padded_text for marker in normalized_markers):
            return True

    return False


def is_explicit_adult_itad_item(item, info=None) -> bool:
    metadata = info or item or {}

    # mature/18+/Nudity는 폭력성이나 일반 등급에도 쓰입니다.
    # 노골적 성적 표식은 계속 제외하되 등급 하나만으로 게임을 막지 않습니다.
    return has_explicit_sexual_content(
        (item or {}).get("title"),
        (item or {}).get("slug"),
        (item or {}).get("tags"),
        metadata.get("title"),
        metadata.get("slug"),
        metadata.get("tags"),
        metadata.get("description"),
    )


def is_explicit_adult_steam_game(details) -> bool:
    details = details or {}

    return has_explicit_sexual_content(
        details.get("name"),
        details.get("short_description"),
        details.get("detailed_description"),
        details.get("about_the_game"),
        details.get("genres"),
        details.get("categories"),
        details.get("content_descriptors"),
    )
