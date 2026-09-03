"""Deterministic initial planning and minimal-change replanning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4
from zoneinfo import ZoneInfo

from adaptsg.domain import (
    Itinerary,
    ItineraryChange,
    ItinerarySegment,
    JourneyRequest,
    ReplanProposal,
    ReplanTrigger,
    SegmentPurpose,
    TravelMode,
    TriggerType,
    Venue,
    VenueCategory,
)
from adaptsg.errors import NoFeasibleItinerary, ReplanLimitReached, ToolUnavailable
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.metrics import calculate_plan_metrics
from adaptsg.tools.routing import RoutingClient
from adaptsg.validation import ItineraryValidator

SINGAPORE = ZoneInfo("Asia/Singapore")


class JourneyPlanner:
    """Build a small itinerary and accept it only after deterministic validation."""

    def __init__(
        self,
        *,
        catalog: VenueCatalog,
        routing: RoutingClient,
        validator: ItineraryValidator,
    ) -> None:
        self.catalog = catalog
        self.routing = routing
        self.validator = validator

    def create(self, request: JourneyRequest, *, parser_source: str = "deterministic") -> Itinerary:
        venues = self._select_initial_venues(request)
        purposes = self._purposes_for(venues)
        candidates = (
            self._schedule(
                request=request,
                venues=venues,
                purposes=purposes,
                parser_source=parser_source,
                modes=modes,
            )
            for modes in product(
                (TravelMode.PUBLIC_TRANSPORT, TravelMode.TAXI),
                repeat=len(venues),
            )
        )
        feasible = [
            itinerary for itinerary in candidates if self.validator.validate(itinerary).valid
        ]
        if not feasible:
            itinerary = self._schedule(
                request=request,
                venues=venues,
                purposes=purposes,
                parser_source=parser_source,
            )
            messages = "; ".join(
                issue.message for issue in self.validator.validate(itinerary).issues
            )
            raise NoFeasibleItinerary(f"no safe initial itinerary: {messages}")
        return min(
            feasible,
            key=lambda itinerary: (
                sum(segment.route.mode is TravelMode.TAXI for segment in itinerary.segments),
                itinerary.total_cost_sgd,
            ),
        )

    def _select_initial_venues(self, request: JourneyRequest) -> tuple[Venue, ...]:
        required = [
            self.catalog.get(venue_id) for venue_id in sorted(request.hard.required_venue_ids)
        ]
        non_food_required = [
            venue for venue in required if venue.category is not VenueCategory.FOOD
        ]
        if len(non_food_required) > request.max_stops - 1:
            raise NoFeasibleItinerary("required venues leave no room for the mandatory lunch stop")

        preferred = [
            self.catalog.get(venue_id)
            for venue_id in sorted(request.soft.preferred_venue_ids)
            if venue_id not in request.hard.required_venue_ids
        ]
        activities: list[Venue] = list(non_food_required)
        for venue in preferred:
            if venue.category is not VenueCategory.FOOD and len(activities) < request.max_stops - 1:
                activities.append(venue)
        defaults = (self.catalog.get("national-gallery"), self.catalog.get("gardens-bay-outdoor"))
        for venue in defaults:
            if len(activities) >= request.max_stops - 1:
                break
            if venue.id not in {item.id for item in activities}:
                activities.append(venue)
        priority = {venue.id: index for index, venue in enumerate(defaults)}
        activities.sort(key=lambda venue: (priority.get(venue.id, len(priority)), venue.id))

        lunch = next(
            (venue for venue in required if venue.category is VenueCategory.FOOD),
            self.catalog.get("funan-food-court"),
        )
        if request.hard.wheelchair_accessible_required:
            eligible_ids = {venue.id for venue in self.catalog.eligible(wheelchair_required=True)}
            selected_ids = {venue.id for venue in (*activities, lunch)}
            if not selected_ids <= eligible_ids:
                raise NoFeasibleItinerary("a required venue lacks verified accessibility data")

        ordered = [activities[0], lunch]
        ordered.extend(activities[1:])
        return tuple(ordered[: request.max_stops])

    @staticmethod
    def _purposes_for(venues: tuple[Venue, ...]) -> tuple[SegmentPurpose, ...]:
        return tuple(
            SegmentPurpose.LUNCH
            if venue.category is VenueCategory.FOOD
            else SegmentPurpose.ACTIVITY
            for venue in venues
        )

    def _schedule(
        self,
        *,
        request: JourneyRequest,
        venues: tuple[Venue, ...],
        purposes: tuple[SegmentPurpose, ...],
        parser_source: str,
        replan_count: int = 0,
        modes: tuple[TravelMode, ...] | None = None,
        durations: tuple[int, ...] | None = None,
        preserved_prefix: tuple[ItinerarySegment, ...] = (),
    ) -> Itinerary:
        segments: list[ItinerarySegment] = list(preserved_prefix)
        if preserved_prefix:
            current_time = preserved_prefix[-1].activity_end
            current_location = preserved_prefix[-1].venue.location
            current_label = preserved_prefix[-1].venue.name
        else:
            current_time = datetime.combine(
                request.journey_date, request.start_time, tzinfo=SINGAPORE
            )
            current_location = request.start_location
            current_label = request.start_label

        start_index = len(preserved_prefix)
        for index in range(start_index, len(venues)):
            venue = venues[index]
            purpose = purposes[index]
            mode = modes[index] if modes else TravelMode.PUBLIC_TRANSPORT
            route = self.routing.route(
                origin_label=current_label,
                origin=current_location,
                destination_label=venue.name,
                destination=venue.location,
                depart_at=current_time,
                mode=mode,
                max_walking_distance_m=request.hard.max_walking_distance_m,
            )
            activity_start = route.arrive_at
            venue_open = datetime.combine(
                request.journey_date, venue.opening_time, tzinfo=SINGAPORE
            )
            if activity_start < venue_open:
                activity_start = venue_open
            duration = durations[index] if durations else venue.average_duration_minutes
            activity_end = activity_start + timedelta(minutes=duration)
            notes: tuple[str, ...] = ()
            if venue.rest_seating:
                notes = ("Rest seating available",)
            segments.append(
                ItinerarySegment(
                    venue=venue,
                    route=route,
                    activity_start=activity_start,
                    activity_end=activity_end,
                    purpose=purpose,
                    notes=notes,
                )
            )
            current_time = activity_end
            current_location = venue.location
            current_label = venue.name

        itinerary = Itinerary(
            request=request,
            segments=tuple(segments),
            total_cost_sgd=0,
            created_at=datetime.now(UTC),
            replan_count=replan_count,
            parser_source=parser_source,
        )
        return itinerary.model_copy(
            update={"total_cost_sgd": calculate_plan_metrics(itinerary).total_cost_sgd}
        )


class JourneyReplanner:
    """Change the smallest affected suffix and score candidates deterministically."""

    def __init__(
        self,
        *,
        planner: JourneyPlanner,
        approval_cost_increase_sgd: float = 8,
        max_replans: int = 2,
    ) -> None:
        self.planner = planner
        self.approval_cost_increase_sgd = approval_cost_increase_sgd
        self.max_replans = max_replans

    def propose(self, itinerary: Itinerary, trigger: ReplanTrigger) -> ReplanProposal:
        if itinerary.replan_count >= self.max_replans:
            raise ReplanLimitReached(f"replanning is capped at {self.max_replans} cycles")

        candidates = self._candidate_itineraries(itinerary, trigger)
        feasible: list[tuple[tuple[object, ...], Itinerary]] = []
        for candidate in candidates:
            validation = self.planner.validator.validate(candidate)
            if validation.valid:
                feasible.append((self._candidate_score(itinerary, candidate), candidate))
        if not feasible:
            raise NoFeasibleItinerary(
                "no safe replan exists without relaxing a hard constraint; user input is required"
            )

        _, selected = min(feasible, key=lambda item: item[0])
        validation = self.planner.validator.validate(selected)
        changes = self._diff(itinerary, selected, trigger.message)
        cost_delta = round(selected.total_cost_sgd - itinerary.total_cost_sgd, 2)
        return ReplanProposal(
            original_itinerary_id=itinerary.id,
            itinerary=selected,
            changes=changes,
            cost_delta_sgd=cost_delta,
            requires_approval=self._requires_approval(itinerary, selected, changes, cost_delta),
            validation=validation,
        )

    def _requires_approval(
        self,
        before: Itinerary,
        after: Itinerary,
        changes: tuple[ItineraryChange, ...],
        cost_delta: float,
    ) -> bool:
        requested_ids = (
            before.request.hard.required_venue_ids | before.request.soft.preferred_venue_ids
        )
        retained_ids = {segment.venue.id for segment in after.segments}
        requested_destination_removed = bool(requested_ids - retained_ids)
        transport_upgrade = any(
            left.route.mode is not TravelMode.TAXI and right.route.mode is TravelMode.TAXI
            for left, right in zip(before.segments, after.segments, strict=False)
        )
        return (
            cost_delta > self.approval_cost_increase_sgd
            or requested_destination_removed
            or transport_upgrade
            or len(changes) > 1
        )

    def _candidate_itineraries(
        self, itinerary: Itinerary, trigger: ReplanTrigger
    ) -> tuple[Itinerary, ...]:
        if trigger.type is TriggerType.FATIGUE:
            return self._fatigue_candidates(itinerary)
        if trigger.type is TriggerType.BUDGET_REDUCTION:
            return self._budget_candidates(itinerary, trigger)
        return self._replacement_candidates(itinerary, trigger)

    def _replacement_candidates(
        self, itinerary: Itinerary, trigger: ReplanTrigger
    ) -> tuple[Itinerary, ...]:
        affected = self._affected_indices(itinerary, trigger)
        if not affected:
            unchanged = itinerary.model_copy(
                update={"id": uuid4(), "replan_count": itinerary.replan_count + 1}
            )
            return (unchanged,)
        first = min(affected)
        original_venues = tuple(segment.venue for segment in itinerary.segments)
        purposes = tuple(segment.purpose for segment in itinerary.segments)
        used_ids = frozenset(venue.id for venue in original_venues)
        replacements = self.planner.catalog.eligible(
            wheelchair_required=itinerary.request.hard.wheelchair_accessible_required,
            indoor_only=trigger.type
            in {TriggerType.HEAVY_RAIN, TriggerType.HIGH_PSI, TriggerType.FLOOD_ALERT},
            excluded_ids=used_ids | trigger.affected_venue_ids,
            categories=(VenueCategory.INDOOR_ATTRACTION, VenueCategory.INDOOR_MUSEUM),
        )
        candidates = []
        for replacement in replacements:
            venues = list(original_venues)
            venues[first] = replacement
            try:
                candidates.append(
                    self.planner._schedule(
                        request=itinerary.request,
                        venues=tuple(venues),
                        purposes=purposes,
                        parser_source=itinerary.parser_source,
                        replan_count=itinerary.replan_count + 1,
                        preserved_prefix=itinerary.segments[:first],
                    )
                )
            except ToolUnavailable:
                continue
        return tuple(candidates)

    def _fatigue_candidates(self, itinerary: Itinerary) -> tuple[Itinerary, ...]:
        if not itinerary.segments:
            return ()
        target = max(
            range(len(itinerary.segments)),
            key=lambda index: itinerary.segments[index].route.walking_distance_m,
        )
        venues = tuple(segment.venue for segment in itinerary.segments)
        purposes = tuple(segment.purpose for segment in itinerary.segments)
        base_modes = tuple(segment.route.mode for segment in itinerary.segments)
        base_durations = tuple(
            round((segment.activity_end - segment.activity_start).total_seconds() / 60)
            for segment in itinerary.segments
        )
        candidates = []
        for start in (target, max(0, target - 1)):
            modes = list(base_modes)
            durations = list(base_durations)
            modes[start] = TravelMode.TAXI
            durations[start] = min(durations[start], 45)
            candidates.append(
                self.planner._schedule(
                    request=itinerary.request,
                    venues=venues,
                    purposes=purposes,
                    parser_source=itinerary.parser_source,
                    replan_count=itinerary.replan_count + 1,
                    modes=tuple(modes),
                    durations=tuple(durations),
                    preserved_prefix=itinerary.segments[:start],
                )
            )
        return tuple(candidates)

    def _budget_candidates(
        self, itinerary: Itinerary, trigger: ReplanTrigger
    ) -> tuple[Itinerary, ...]:
        if trigger.new_budget_sgd is None:
            raise NoFeasibleItinerary("a budget reduction requires a new budget amount")
        hard = itinerary.request.hard.model_copy(
            update={"total_budget_sgd": trigger.new_budget_sgd}
        )
        request = itinerary.request.model_copy(update={"hard": hard})
        venues = [segment.venue for segment in itinerary.segments]
        purposes = tuple(segment.purpose for segment in itinerary.segments)
        candidates = []
        low_cost = self.planner.catalog.get("esplanade")
        for index, segment in enumerate(itinerary.segments):
            if segment.purpose is SegmentPurpose.LUNCH:
                continue
            replacement = list(venues)
            replacement[index] = low_cost
            candidates.append(
                self.planner._schedule(
                    request=request,
                    venues=tuple(replacement),
                    purposes=purposes,
                    parser_source=itinerary.parser_source,
                    replan_count=itinerary.replan_count + 1,
                    preserved_prefix=itinerary.segments[:index],
                )
            )
        return tuple(candidates)

    @staticmethod
    def _affected_indices(itinerary: Itinerary, trigger: ReplanTrigger) -> tuple[int, ...]:
        indices = []
        for index, segment in enumerate(itinerary.segments):
            affected = segment.venue.id in trigger.affected_venue_ids
            if trigger.type in {TriggerType.HEAVY_RAIN, TriggerType.HIGH_PSI}:
                affected = affected or not segment.venue.indoor
            elif trigger.type in {
                TriggerType.FLOOD_ALERT,
                TriggerType.VENUE_CLOSURE,
                TriggerType.TRANSPORT_DISRUPTION,
            }:
                affected = affected or (
                    not trigger.affected_venue_ids
                    and trigger.type is TriggerType.TRANSPORT_DISRUPTION
                )
            if affected and segment.purpose is not SegmentPurpose.LUNCH:
                indices.append(index)
        return tuple(indices)

    @staticmethod
    def _candidate_score(before: Itinerary, after: Itinerary) -> tuple[object, ...]:
        changed = 0
        retained = min(len(before.segments), len(after.segments))
        for index in range(retained):
            left, right = before.segments[index], after.segments[index]
            if left.venue.id != right.venue.id or left.route.mode != right.route.mode:
                changed += 1
        changed += abs(len(before.segments) - len(after.segments))
        walking = sum(segment.route.walking_distance_m for segment in after.segments)
        positive_cost_delta = max(0.0, after.total_cost_sgd - before.total_cost_sgd)
        preferred_ids = after.request.soft.preferred_venue_ids
        preferred_categories = after.request.soft.preferred_categories
        preference_penalty = sum(
            0
            if segment.venue.id in preferred_ids or segment.venue.category in preferred_categories
            else 1
            for segment in after.segments
        )
        venue_ids = tuple(segment.venue.id for segment in after.segments)
        return (changed, walking, positive_cost_delta, preference_penalty, venue_ids)

    @staticmethod
    def _diff(before: Itinerary, after: Itinerary, reason: str) -> tuple[ItineraryChange, ...]:
        changes = []
        for index, (left, right) in enumerate(zip(before.segments, after.segments, strict=True)):
            left_duration = round((left.activity_end - left.activity_start).total_seconds() / 60)
            right_duration = round((right.activity_end - right.activity_start).total_seconds() / 60)
            if (
                left.venue.id != right.venue.id
                or left.route.mode != right.route.mode
                or left_duration != right_duration
            ):
                before_label = (
                    f"{left.venue.name} via {left.route.mode.value} ({left_duration} min)"
                )
                after_label = (
                    f"{right.venue.name} via {right.route.mode.value} ({right_duration} min)"
                )
                changes.append(
                    ItineraryChange(
                        segment_index=index,
                        before=before_label,
                        after=after_label,
                        reason=reason,
                    )
                )
        return tuple(changes)
