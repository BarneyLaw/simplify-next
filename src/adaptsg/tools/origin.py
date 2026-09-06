"""Pure normalisation and ranking for free-text start locations.

OneMap indexes each MRT line code as its own row, so the way a station is
signed in real life -- ``Bishan MRT Station (NS17/CC15)`` -- matches nothing.
These helpers turn one typed origin into a small, ordered set of queries the
gazetteer can actually answer, and rank whatever comes back deterministically.
No HTTP happens here; ``AdaptSGService._resolve_start_location`` owns the calls.
"""

from __future__ import annotations

import re

from adaptsg.domain import Location, LocationSearchResult
from adaptsg.tools.routing import distance_metres

# Verified against OneMap: this label returns exactly one exact result.
DEFAULT_ORIGIN_LABEL = "Dhoby Ghaut MRT Station (NS24)"
DEFAULT_ORIGIN_LOCATION = Location(lat=1.29870, lng=103.8461)

# Bound the ladder: every extra rung is another eight-second live request.
MAX_ORIGIN_QUERIES = 4

# Two entrances of one station are the same origin for planning purposes.
SAME_PLACE_RADIUS_M = 150

_MAX_LABEL_LENGTH = 120
_BRACKETED = re.compile(r"\s*\([^)]*\)")
_STATION_CODES = re.compile(r"\(\s*([A-Z]{2}\d{1,3})(?:\s*/\s*[A-Z]{2}\d{1,3})+\s*\)")
_STATION_WORD = re.compile(r"\b(?:mrt|lrt|station|interchange)\b", re.IGNORECASE)
_EXIT_SUFFIX = re.compile(r"\bexit\b", re.IGNORECASE)

# Phrases that name no place. Sending these to a gazetteer is guaranteed noise,
# so they resolve to the documented default hub instead of failing the plan.
_VAGUE_ORIGIN = re.compile(
    r"^(?:"
    r"(?:any|a|some)?\s*(?:convenient|central|suitable|accessible|nearby|good)\s+"
    r"(?:\w+\s+){0,2}(?:mrt|lrt)?\s*(?:station|stop|point|location|place)"
    r"|(?:an?\s+)?(?:mrt|lrt)\s+station"
    r"|somewhere\s+\w+"
    r"|anywhere\b.*"
    r"|nearby"
    r"|wherever\b.*"
    r")\W*$",
    re.IGNORECASE,
)


def is_vague_origin(label: str) -> bool:
    """True when the text names no specific place and must not be geocoded."""
    cleaned = label.strip()
    return not cleaned or bool(_VAGUE_ORIGIN.match(cleaned))


def _strip_brackets(label: str) -> str:
    return re.sub(r"\s{2,}", " ", _BRACKETED.sub("", label)).strip()


def origin_query_variants(label: str) -> tuple[str, ...]:
    """Ordered, de-duplicated queries to try for one typed origin.

    Ordered most faithful first, so a label that already resolves is never
    broadened. Capped at ``MAX_ORIGIN_QUERIES`` to bound live requests.
    """
    cleaned = " ".join(label.split())[:_MAX_LABEL_LENGTH].strip()
    if not cleaned:
        return ()
    variants = [cleaned]
    # "(NS17/CC15)" is signage, not a gazetteer entry; the first code is.
    single_code = _STATION_CODES.sub(lambda match: f"({match.group(1)})", cleaned)
    if single_code != cleaned:
        variants.append(single_code)
    bare = _strip_brackets(cleaned)
    if bare and bare != cleaned:
        variants.append(bare)
    # Only for a short bare name: appending "MRT Station" to a long place name
    # would silently retarget a real venue at a nearby station.
    if bare and not _STATION_WORD.search(bare) and len(bare.split()) <= 2:
        variants.append(f"{bare} MRT Station")
    seen: set[str] = set()
    ordered: list[str] = []
    for variant in variants:
        folded = variant.casefold()
        if variant and folded not in seen:
            seen.add(folded)
            ordered.append(variant)
    return tuple(ordered[:MAX_ORIGIN_QUERIES])


def _tier(query: str, result: LocationSearchResult) -> int:
    normalized = query.strip().casefold()
    label = result.label.strip().casefold()
    if label == normalized:
        return 0
    if _strip_brackets(result.label).casefold() == _strip_brackets(query).casefold():
        return 1
    if label.startswith(normalized) and not _EXIT_SUFFIX.search(result.label):
        return 2
    return 3


def rank_origin_candidates(
    query: str, results: tuple[LocationSearchResult, ...]
) -> tuple[LocationSearchResult, ...]:
    """Order results best-match first without discarding any of them."""
    return tuple(
        result
        for _, result in sorted(
            enumerate(results),
            key=lambda pair: (_tier(query, pair[1]), pair[0]),
        )
    )


def is_confident(query: str, ranked: tuple[LocationSearchResult, ...]) -> bool:
    """True when one place is clearly meant, so the user need not be asked."""
    if not ranked:
        return False
    if _tier(query, ranked[0]) <= 1:
        return True
    return all(
        distance_metres(ranked[0].location, other.location) <= SAME_PLACE_RADIUS_M
        for other in ranked[1:]
    )
