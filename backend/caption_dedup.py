"""
Keyword extraction and Jaccard similarity for caption de-duplication.
No external APIs.
"""

from __future__ import annotations

import json
import re
from typing import List, Set

_STOP = frozenset(
    """
    the a an and or but if in on at to for of as is was are were been be being
    has have had having do does did doing will would could should may might must
    not no nor so than then too very just also only even much such both each few
    more most other some that this these those what which who whom whose when where
    why how all any both each every few many most other several some such your our
    their its his her they them we us you it its from with without about into through
    during before after above below between under again further once here there when
    where why how up down out off over own same so than too very can just don now
    get got make made like go going come came see know take use find give tell work
    seem feel try leave call keep let begin help show hear play run move live believe
    hold bring happen stand lose pay meet include continue set learn change lead
    understand watch follow stop create speak read allow add spend grow open walk win
    offer remember love consider appear buy wait serve die send expect build stay
    fall cut reach kill remain suggest raise pass sell require report role decide
    pull""".split()
)


def extract_keywords(text: str, max_keywords: int = 10) -> List[str]:
    """5–10 topical keywords from caption (lowercase, no stopwords)."""
    if not text or not str(text).strip():
        return []
    words = re.findall(r"[a-z0-9']+", str(text).lower())
    seen: Set[str] = set()
    out: List[str] = []
    for w in words:
        if len(w) < 3 or w in _STOP or w.isdigit():
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= max_keywords:
            break
    return out


def keyword_set(text: str) -> Set[str]:
    return set(extract_keywords(text, max_keywords=50))


def jaccard_keyword_similarity(a: str, b: str) -> float:
    """Jaccard similarity on keyword sets (0..1)."""
    A, B = keyword_set(a), keyword_set(b)
    if not A and not B:
        return 0.0
    inter = len(A & B)
    union = len(A | B)
    return float(inter) / float(union) if union else 0.0


def max_similarity_vs_recent(caption: str, recent_captions: List[str]) -> float:
    if not recent_captions:
        return 0.0
    return max((jaccard_keyword_similarity(caption, prev) for prev in recent_captions if prev), default=0.0)


def keywords_to_json(keywords: List[str]) -> str:
    return json.dumps(keywords[:20])


def keywords_from_json(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return list(data) if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def post_type_display(internal_type: str) -> str:
    """Human-readable label for post_history.post_type."""
    m = {
        "morning_promo": "MORNING PROMO",
        "afternoon_tip": "TIP",
        "evening_proof": "EVENING SOCIAL PROOF",
    }
    return m.get((internal_type or "").strip(), (internal_type or "POST").upper().replace("_", " "))
