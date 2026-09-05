"""Pure HTML component renderers for the Streamlit redesign.

Nothing here decides safety, routing, accessibility or approval. Every function formats
state the domain, tools and validator have already produced, per the `AGENTS.md:33`
presentation boundary. These are the components `streamlit_app.py` injects with
`st.html()`; widgets (buttons, text areas) cannot live inside an `st.html` block and stay
in `streamlit_app.py` itself.
"""

from __future__ import annotations

import base64
import importlib.resources
from datetime import date, datetime, time
from functools import lru_cache
from html import escape
from math import cos, radians
from uuid import UUID
from zoneinfo import ZoneInfo

from adaptsg.domain import (
    AccessibilityStatus,
    HardConstraints,
    Itinerary,
    ItinerarySegment,
    ReplanProposal,
    SegmentPurpose,
    TravelMode,
    ValidationResult,
    Venue,
)
from adaptsg.presentation import mode_badge, provenance_label

SINGAPORE = ZoneInfo("Asia/Singapore")

MODE_LABEL: dict[TravelMode, str] = {
    TravelMode.WALK: "On foot",
    TravelMode.PUBLIC_TRANSPORT: "MRT and bus",
    TravelMode.TAXI: "Taxi",
}

ICON_CHECK = (
    '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true" '
    'focusable="false"><rect x="1.5" y="1.5" width="13" height="13" rx="2" '
    'stroke="currentColor" stroke-width="1.6"/><path d="m4.6 8.2 2.3 2.4 4.5-5" '
    'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)
ICON_WARN = (
    '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true" '
    'focusable="false"><path d="M8 1.9 15 14H1L8 1.9Z" stroke="currentColor" '
    'stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6.2v3.1" '
    'stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
    '<circle cx="8" cy="11.6" r="1" fill="currentColor"/></svg>'
)
ICON_CROSS = (
    '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true" '
    'focusable="false"><rect x="1.5" y="1.5" width="13" height="13" rx="2" '
    'stroke="currentColor" stroke-width="1.6"/><path d="m5.2 5.2 5.6 5.6M10.8 5.2l-5.6 5.6" '
    'stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>'
)
ICON_SEAT = (
    '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true" '
    'focusable="false"><path d="M4 3v5.5h8V3M2.8 8.5h10.4M4.4 8.5V13M11.6 8.5V13" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)
ICON_CLOCK = (
    '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true" '
    'focusable="false"><circle cx="8" cy="8" r="6.2" stroke="currentColor" '
    'stroke-width="1.8"/></svg>'
)
ICON_CHEVRON = (
    '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true" '
    'focusable="false"><path d="m5.5 3.5 5 4.5-5 4.5" stroke="var(--blue-600)" '
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
ICON_LOCK = (
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true" '
    'focusable="false"><rect x="3" y="7" width="10" height="7" rx="1.6" '
    'stroke="var(--muted)" stroke-width="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" '
    'stroke="var(--muted)" stroke-width="1.5" stroke-linecap="round"/></svg>'
)


def _money(value: float) -> str:
    return f"S${value:.2f}"


def _time_of_day(value: time) -> str:
    hour12 = ((value.hour + 11) % 12) + 1
    period = "pm" if value.hour >= 12 else "am"
    return f"{hour12}:{value.minute:02d}{period}"


def _clock(value: datetime) -> str:
    return _time_of_day(value.astimezone(SINGAPORE).time())


def _long_date(value: date) -> str:
    return f"{value.strftime('%A')} {value.day} {value.strftime('%B')} {value.year}"


def _ev_timestamp(value: datetime) -> str:
    return value.astimezone(SINGAPORE).strftime("%d %b %Y %H:%M +08")


def _open_hours(venue: Venue) -> str:
    opens = venue.opening_time.strftime("%H:%M")
    closes = venue.closing_time.strftime("%H:%M")
    return f"{opens}\u2013{closes}"


def _duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if not hours:
        return f"{mins} minute{'' if mins == 1 else 's'}"
    if not mins:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{hours} hour{'' if hours == 1 else 's'} {mins} minute{'' if mins == 1 else 's'}"


def _walking_percent(distance: int, limit: int) -> int:
    if limit <= 0:
        return 100
    return min(100, round(distance / limit * 100))


def _longest_leg(itinerary: Itinerary) -> int:
    return max((segment.route.walking_distance_m for segment in itinerary.segments), default=0)


def _chip(kind: str, icon: str, text: str) -> str:
    spacer = " " if icon else ""
    return f'<span class="chip {kind}">{icon}{spacer}{escape(text)}</span>'


def _access_chip(venue: Venue) -> str:
    if venue.accessibility_status is AccessibilityStatus.VERIFIED and venue.accessibility_source:
        return _chip("pass", ICON_CHECK, "Wheelchair access checked")
    if venue.accessibility_status is AccessibilityStatus.INACCESSIBLE:
        return _chip("breach", ICON_CROSS, "Not wheelchair accessible")
    return _chip("caution", ICON_WARN, "Wheelchair access not confirmed")


def _freshness_chip(freshness: object) -> str:
    value = getattr(freshness, "value", freshness)
    if value == "stale":
        return _chip("caution", ICON_CLOCK, "Not checked recently")
    if value == "unavailable":
        return _chip("caution", ICON_CLOCK, "Could not be checked")
    return ""


def _purpose_chip(segment: ItinerarySegment, hard: HardConstraints) -> str:
    if segment.purpose is SegmentPurpose.LUNCH:
        return _chip("blue", "", f"Lunch — before {_time_of_day(hard.lunch_latest)}, as you asked")
    if segment.purpose is SegmentPurpose.REST:
        return _chip("plain", "", "Rest stop")
    return _chip("plain", "", "Somewhere to look around")


def _stop_badges(segment: ItinerarySegment, hard: HardConstraints) -> str:
    chips = [_access_chip(segment.venue)]
    if segment.venue.rest_seating:
        chips.append(_chip("plain", ICON_SEAT, "Somewhere to sit down"))
    if segment.venue.indoor is False:
        chips.append(_chip("plain", "", "Outdoors"))
    chips.append(_purpose_chip(segment, hard))
    return "".join(chips)


@lru_cache(maxsize=1)
def _mascot_data_uri() -> str:
    payload = importlib.resources.files("adaptsg.data").joinpath("mascot.png").read_bytes()
    return f"data:image/png;base64,{base64.b64encode(payload).decode('ascii')}"


def brand_mark(size: int = 30) -> str:
    """The AdaptSG mascot mark, sized for the sidebar rail or a hero heading."""
    return (
        f'<img class="brand-mark" src="{_mascot_data_uri()}" width="{size}" '
        f'alt="" aria-hidden="true">'
    )


def chip_row(chips: list[tuple[str, str, str]]) -> str:
    """Render a sequence of `(kind, icon, text)` as adjoining `.chip` spans."""
    return "".join(_chip(kind, icon, text) for kind, icon, text in chips)


def must_haves(hard: HardConstraints) -> str:
    """The rail's locked must-haves list; `rest_interval_minutes` is a row, not a caption."""
    rows = [
        ("Wheelchair access", "Needed" if hard.wheelchair_accessible_required else "Not required"),
        ("Longest walk at once", f"{hard.max_walking_distance_m} m"),
        ("Lunch before", _time_of_day(hard.lunch_latest)),
        ("Home by", _time_of_day(hard.finish_by)),
        ("Rest break every", f"{hard.rest_interval_minutes} min"),
        ("Budget for the day", _money(hard.total_budget_sgd)),
    ]
    items = "".join(
        f'<li>{ICON_LOCK}<span class="k">{escape(key)}</span>'
        f'<span class="v num">{escape(value)}</span></li>'
        for key, value in rows
    )
    if hard.required_venue_ids:
        names = ", ".join(sorted(hard.required_venue_ids))
        items += (
            f'<li>{ICON_LOCK}<span class="k">Must include</span>'
            f'<span class="v">{escape(names)}</span></li>'
        )
    return f'<ul class="locked">{items}</ul>'


def date_and_start(journey_date: date, start_label: str, start_time: time) -> str:
    """The `<strong>date</strong> · Starting from X at Y` line under a page heading."""
    return (
        f'<div class="daterow"><strong>{escape(_long_date(journey_date))}</strong>'
        f'<span class="sep">&middot;</span>'
        f"<span>Starting from {escape(start_label)} at {_time_of_day(start_time)}</span></div>"
    )


def finish_note(itinerary: Itinerary) -> str:
    if not itinerary.segments:
        return ""
    hard = itinerary.request.hard
    last = itinerary.segments[-1]
    return (
        f'<p class="note">Finishes at {_clock(last.activity_end)}, before the '
        f"{_time_of_day(hard.finish_by)} you asked to be home by.</p>"
    )


def timeline(itinerary: Itinerary) -> str:
    """The `<ol>` of travel legs and stop cards that replaces the old `st.dataframe`."""
    hard = itinerary.request.hard
    rows: list[str] = []
    for index, segment in enumerate(itinerary.segments):
        route = segment.route
        pct = _walking_percent(route.walking_distance_m, hard.max_walking_distance_m)
        origin_label = (
            f"<span>From {escape(itinerary.request.start_label)}</span>"
            f'<span class="sep">&middot;</span>'
            if index == 0
            else ""
        )
        mode_label = MODE_LABEL.get(route.mode, route.mode.value)
        travel_time = _duration(route.duration_minutes)
        rows.append(
            '<li class="leg"><div class="when"></div><div class="spine"></div>'
            f'<div class="legbody">{origin_label}'
            f"<span><strong>{escape(mode_label)}</strong>, {travel_time}</span>"
            '<span class="sep">&middot;</span>'
            f'<span class="meter"><span class="track"><i style="width:{pct}%"></i></span>'
            f'<span class="num">{route.walking_distance_m} m walking</span></span>'
            f'<span class="sep">&middot;</span><span class="num">'
            f"{_money(route.estimated_cost_sgd)}</span>"
            f"{_freshness_chip(route.freshness)}</div></li>"
        )
        venue = segment.venue
        cost_label = "About lunch" if segment.purpose is SegmentPurpose.LUNCH else "Cost to get in"
        cost = "Free" if venue.estimated_cost_sgd == 0 else _money(venue.estimated_cost_sgd)
        minutes = round((segment.activity_end - segment.activity_start).total_seconds() / 60)
        rows.append(
            '<li class="stop"><div class="when">'
            f'<div class="t1 num">{_clock(segment.activity_start)}</div>'
            f'<div class="t2 num">to {_clock(segment.activity_end)}</div></div>'
            '<div class="spine"><span class="dot"></span></div>'
            f'<div class="card"><div class="cardtop"><h3>{escape(venue.name)}</h3>'
            f'<div class="badges">{_stop_badges(segment, hard)}</div></div>'
            '<div class="facts"><div class="fact"><span class="k">Time there</span>'
            f'<span class="v">{_duration(minutes)}</span></div>'
            f'<div class="fact"><span class="k">{cost_label}</span>'
            f'<span class="v num">{cost}</span></div></div></div></li>'
        )
    return f'<ol class="tl">{"".join(rows)}</ol>'


def summary(itinerary: Itinerary, *, show_replans: bool = False) -> str:
    hard = itinerary.request.hard
    longest = _longest_leg(itinerary)
    over = longest > hard.max_walking_distance_m
    pct = _walking_percent(longest, hard.max_walking_distance_m)
    track_class = "track over" if over else "track"
    num_style = ' style="color:var(--breach);font-weight:600"' if over else ""
    walked_label = (
        f"{longest} m — longer than {hard.max_walking_distance_m} m you allowed"
        if over
        else f"{longest} m of {hard.max_walking_distance_m} m"
    )
    items = [
        '<div class="sitem"><div class="k">Whole day costs</div>'
        f'<div class="v num">{_money(itinerary.total_cost_sgd)} '
        f"<small>of your {_money(hard.total_budget_sgd)}</small></div></div>",
        '<div class="sitem"><div class="k">Places you will visit</div>'
        f'<div class="v num">{len(itinerary.segments)}</div></div>',
        '<div class="sitem"><div class="k">Most walking at once</div><div class="v">'
        f'<span class="meter"><span class="{track_class}"><i style="width:{pct}%"></i></span>'
        f'<span class="num"{num_style}>{walked_label}</span></span></div></div>',
    ]
    if show_replans:
        items.append(
            '<div class="sitem"><div class="k">Changes made so far</div>'
            f'<div class="v num">{itinerary.replan_count}</div></div>'
        )
    return f'<div class="summary">{"".join(items)}</div>'


def _validation_sentence(validation: ValidationResult) -> str:
    if validation.valid:
        return "AdaptSG checked the changed plan against your must-haves. Every one still holds."
    issues = "; ".join(issue.message for issue in validation.issues)
    return f"AdaptSG found {len(validation.issues)} issue(s) with this change: {issues}."


def _venue_row(v: Venue) -> str:
    reviewed = str(v.data_reviewed_on) if v.data_reviewed_on else "not recorded"
    return (
        f"<dt>{escape(v.name)}</dt>"
        f"<dd>{escape(v.id)} &middot; {escape(v.category.value)} &middot; "
        f"accessibility_status: {escape(v.accessibility_status.value)} &middot; "
        f"accessibility_source: {escape(v.accessibility_source or 'not recorded')} &middot; "
        f"data_reviewed_on: {escape(reviewed)} &middot; "
        f"{v.location.lat}, {v.location.lng} &middot; open {_open_hours(v)} &middot; "
        f"rest_seating: {v.rest_seating}</dd>"
    )


def evidence(
    itinerary: Itinerary,
    *,
    mode: str,
    storage: str,
    journey_id: UUID,
    version: int,
    status: str,
    expires_at: datetime,
) -> str:
    """The two-tier evidence panel: plain sentences, then per-route and per-venue detail."""
    travel_total = sum(segment.route.estimated_cost_sgd for segment in itinerary.segments)
    places_total = sum(segment.venue.estimated_cost_sgd for segment in itinerary.segments)
    route_rows = "".join(
        f"<dt>Route to {escape(segment.venue.name)}</dt>"
        f"<dd>{escape(segment.route.source)} &middot; "
        f"{escape(_ev_timestamp(segment.route.source_timestamp))} &middot; "
        f"freshness: {escape(segment.route.freshness.value)}</dd>"
        for segment in itinerary.segments
    )
    venue_rows = "".join(_venue_row(segment.venue) for segment in itinerary.segments)
    return (
        f"<details><summary>{ICON_CHEVRON} Where these numbers come from</summary>"
        '<div class="dbody">'
        '<p class="note">You do not need to read this to decide. It is here so the plan '
        "can be checked.</p>"
        '<dl class="ev plain">'
        f"<dt>Travel times and fares</dt><dd>{escape(provenance_label(itinerary, mode=mode))}</dd>"
        "<dt>The places</dt><dd>Every stop came from AdaptSG&#39;s curated catalog, each "
        "with wheelchair access recorded.</dd>"
        "<dt>Safety checks</dt><dd>This plan was checked against your must-haves before "
        "being offered, and it passed.</dd>"
        f"<dt>Cost of the day</dt><dd>{_money(itinerary.total_cost_sgd)} &mdash; travel "
        f"{_money(travel_total)} and places {_money(places_total)}.</dd>"
        f"<dt>This plan is kept until</dt><dd>{escape(_ev_timestamp(expires_at))}</dd>"
        "</dl>"
        '<details class="tech"><summary>Technical details (for reviewers)</summary>'
        f'<dl class="ev">{route_rows}{venue_rows}'
        f"<dt>Runtime mode</dt><dd>{escape(mode_badge(mode))}</dd>"
        f"<dt>Storage</dt><dd>{escape(storage)}</dd>"
        f"<dt>Plan reference</dt><dd>journey {journey_id} &middot; "
        f"version {version} &middot; status {escape(status)} &middot; "
        f"expires_at {escape(_ev_timestamp(expires_at))}</dd>"
        "</dl></details></div></details>"
    )


def map_svg(itinerary: Itinerary) -> str:
    """An inline SVG sketch of stop order (decorative — every pin is already a list row)."""
    request = itinerary.request
    start = request.start_location
    points: list[tuple[str, float, float, str]] = [
        (f"Start — {request.start_label}", start.lat, start.lng, "S")
    ]
    for index, segment in enumerate(itinerary.segments, start=1):
        points.append(
            (segment.venue.name, segment.venue.location.lat, segment.venue.location.lng, str(index))
        )
    lat_avg = sum(lat for _label, lat, _lng, _tag in points) / len(points)
    scale = cos(radians(lat_avg)) or 1
    xs = [lng * scale for _label, _lat, lng, _tag in points]
    ys = [-lat for _label, lat, _lng, _tag in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    pad = 0.14
    positioned = [
        (
            label,
            tag,
            (pad + (1 - 2 * pad) * ((x - min_x) / span_x)) * 100,
            (pad + (1 - 2 * pad) * ((y - min_y) / span_y)) * 100,
        )
        for (label, _lat, _lng, tag), x, y in zip(points, xs, ys, strict=True)
    ]
    pins = "".join(
        f'<div class="pin" style="left:{left:.1f}%;top:{top:.1f}%">'
        f'<b aria-hidden="true">{escape(tag)}</b><span>{escape(label)}</span></div>'
        for label, tag, left, top in positioned
    )
    path_d = " ".join(
        f"{'M' if i == 0 else 'L'}{left:.1f} {top:.1f}"
        for i, (_label, _tag, left, top) in enumerate(positioned)
    )
    return (
        '<div class="map"><div class="maphead"><h2 style="font-size:18px">'
        "Where you are going</h2></div>"
        f'<div class="mapcanvas" aria-hidden="true">{pins}'
        '<svg viewBox="0 0 100 100" preserveAspectRatio="none" '
        'style="position:absolute;inset:0;width:100%;height:100%">'
        f'<path d="{path_d}" fill="none" stroke="var(--blue-200)" stroke-width="1.2" '
        'stroke-dasharray="3 3"/></svg></div>'
        '<p class="mapnote"><strong>Not to scale.</strong> A rough picture of where the '
        "stops sit relative to one another. Every stop is listed in order on the "
        "left.</p></div>"
    )


def _diff_card(
    index: int, kind_class: str, label: str, segment: ItinerarySegment, hard: HardConstraints
) -> str:
    venue = segment.venue
    cost = "Free" if venue.estimated_cost_sgd == 0 else _money(venue.estimated_cost_sgd)
    band_class = f"band {kind_class}".strip()
    return (
        f'<div class="{band_class}"><p class="eyebrow">Stop {index + 1} — {label}</p>'
        f'<h3>{escape(venue.name)}</h3><div class="badges">{_stop_badges(segment, hard)}</div>'
        f'<p class="num">{_clock(segment.activity_start)} to {_clock(segment.activity_end)} '
        f"&middot; {segment.route.walking_distance_m} m walking &middot; {cost}</p></div>"
    )


def proposal_diff(current: Itinerary, proposal: ReplanProposal) -> str:
    """The before/after stop cards for a replan proposal, diffed by `changes[].segment_index`.

    The rich cards cannot come from `changes[].before`/`after`, which are single strings
    whose `reason` the server overwrites (`agent.py:833-839`) — the real segments are read
    from `current` and `proposal.itinerary` instead.
    """
    hard = current.request.hard
    if not proposal.changes:
        body = "<p>No stop changed for this update.</p>"
    else:
        body = "".join(
            '<div class="grid2" style="margin-top:8px">'
            + _diff_card(
                change.segment_index, "", "now", current.segments[change.segment_index], hard
            )
            + _diff_card(
                change.segment_index,
                "blue",
                "suggested",
                proposal.itinerary.segments[change.segment_index],
                hard,
            )
            + "</div>"
            for change in proposal.changes
        )
    before_longest = _longest_leg(current)
    after_longest = _longest_leg(proposal.itinerary)
    before_finish = _clock(current.segments[-1].activity_end)
    after_finish = _clock(proposal.itinerary.segments[-1].activity_end)
    facts = (
        '<div class="facts" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-top:12px">'
        '<div class="fact"><span class="k">Cost of the day</span>'
        f'<span class="v num">{_money(current.total_cost_sgd)} &rarr; '
        f"{_money(proposal.itinerary.total_cost_sgd)}</span></div>"
        '<div class="fact"><span class="k">Most walking at once</span>'
        f'<span class="v num">{before_longest} m &rarr; {after_longest} m</span></div>'
        '<div class="fact"><span class="k">Finishes</span>'
        f'<span class="v num">{before_finish} &rarr; {after_finish}</span></div></div>'
        f'<p class="note" style="margin-top:10px">'
        f"{escape(_validation_sentence(proposal.validation))}</p>"
    )
    return f"<h2>What would change</h2>{body}{facts}"
