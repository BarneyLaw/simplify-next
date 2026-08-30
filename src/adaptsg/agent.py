"""Bounded LangGraph orchestration and the public AdaptSG service facade."""

from __future__ import annotations

from datetime import date
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from adaptsg.domain import (
    EnvironmentSnapshot,
    Itinerary,
    MonitoringOutcome,
    ParseOutcome,
    PlanOutcome,
    ReplanProposal,
    ReplanTrigger,
    TriggerType,
)
from adaptsg.errors import NoFeasibleItinerary
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import BedrockPreferenceParser, PreferenceParser
from adaptsg.settings import Settings, get_settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.environment import (
    DemoEnvironmentClient,
    EnvironmentClient,
    LiveEnvironmentClient,
)
from adaptsg.tools.routing import DemoRoutingClient, OneMapRoutingClient
from adaptsg.validation import ItineraryValidator


class PlanGraphState(TypedDict):
    prompt: str
    journey_date: date
    parsed: NotRequired[ParseOutcome]
    itinerary: NotRequired[Itinerary]
    error: NotRequired[str]


class AdaptSGService:
    def __init__(
        self,
        *,
        parser: PreferenceParser,
        planner: JourneyPlanner,
        replanner: JourneyReplanner,
        environment: EnvironmentClient,
    ) -> None:
        self.parser = parser
        self.planner = planner
        self.replanner = replanner
        self.environment = environment
        self._plan_graph = self._build_plan_graph()

    def _build_plan_graph(self) -> object:
        graph = StateGraph(PlanGraphState)

        def parse_node(state: PlanGraphState) -> dict[str, object]:
            try:
                parsed = self.parser.parse(state["prompt"], journey_date=state["journey_date"])
                return {"parsed": parsed}
            except Exception as exc:  # boundary: translate provider errors to typed graph state
                return {"error": f"constraint parsing failed: {exc}"}

        def plan_node(state: PlanGraphState) -> dict[str, object]:
            if "error" in state:
                return {}
            parsed = state["parsed"]
            try:
                itinerary = self.planner.create(
                    parsed.request,
                    parser_source=parsed.source,
                )
                return {"itinerary": itinerary}
            except NoFeasibleItinerary as exc:
                return {"error": str(exc)}

        graph.add_node("parse_preferences", parse_node)
        graph.add_node("plan_and_validate", plan_node)
        graph.add_edge(START, "parse_preferences")
        graph.add_edge("parse_preferences", "plan_and_validate")
        graph.add_edge("plan_and_validate", END)
        return graph.compile()

    def create_plan(self, prompt: str, *, journey_date: date) -> PlanOutcome:
        result = cast(
            PlanGraphState,
            self._plan_graph.invoke({"prompt": prompt, "journey_date": journey_date}),  # type: ignore[attr-defined]
        )
        if "error" in result:
            raise NoFeasibleItinerary(result["error"])
        parsed = result["parsed"]
        return PlanOutcome(
            itinerary=result["itinerary"],
            warnings=parsed.warnings,
            token_usage=parsed.token_usage,
        )

    def propose_replan(self, itinerary: Itinerary, trigger: ReplanTrigger) -> ReplanProposal:
        return self.replanner.propose(itinerary, trigger)

    def monitor(self, itinerary: Itinerary) -> MonitoringOutcome:
        snapshot = self.environment.current()
        return MonitoringOutcome(
            snapshot=snapshot,
            triggers=self._environment_triggers(itinerary, snapshot),
        )

    @staticmethod
    def _environment_triggers(
        itinerary: Itinerary, snapshot: EnvironmentSnapshot
    ) -> tuple[ReplanTrigger, ...]:
        triggers = []
        weather = snapshot.weather_summary.casefold()
        if any(term in weather for term in ("heavy rain", "thunder", "showers")):
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.HEAVY_RAIN,
                    message=f"Weather update: {snapshot.weather_summary}",
                )
            )
        if snapshot.psi >= 101:
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.HIGH_PSI,
                    message=f"24-hour PSI reached {snapshot.psi}",
                )
            )
        itinerary_ids = frozenset(segment.venue.id for segment in itinerary.segments)
        affected = itinerary_ids & snapshot.flood_affected_venue_ids
        if affected:
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.FLOOD_ALERT,
                    message="PUB flood alert intersects the journey",
                    affected_venue_ids=affected,
                )
            )
        if snapshot.disrupted_route_labels:
            labels = ", ".join(sorted(snapshot.disrupted_route_labels))
            triggers.append(
                ReplanTrigger(
                    type=TriggerType.TRANSPORT_DISRUPTION,
                    message=f"LTA transport disruption: {labels}",
                )
            )
        return tuple(triggers)


def build_service(settings: Settings | None = None) -> AdaptSGService:
    resolved = settings or get_settings()
    catalog = VenueCatalog()
    validator = ItineraryValidator(max_replans=resolved.adaptsg_max_replans)
    routing = (
        DemoRoutingClient()
        if resolved.adaptsg_mode == "demo"
        else OneMapRoutingClient(
            token=resolved.onemap_api_token or "",
            bfa_enabled=resolved.onemap_bfa_enabled,
        )
    )
    environment: EnvironmentClient = (
        DemoEnvironmentClient()
        if resolved.adaptsg_mode == "demo"
        else LiveEnvironmentClient(
            catalog=catalog,
            lta_account_key=resolved.lta_account_key or "",
            data_gov_api_key=resolved.data_gov_sg_api_key,
        )
    )
    planner = JourneyPlanner(
        catalog=catalog,
        routing=routing,
        validator=validator,
    )
    replanner = JourneyReplanner(
        planner=planner,
        approval_cost_increase_sgd=resolved.adaptsg_approval_cost_increase_sgd,
        max_replans=resolved.adaptsg_max_replans,
    )
    parser = BedrockPreferenceParser(settings=resolved, catalog=catalog)
    return AdaptSGService(
        parser=parser,
        planner=planner,
        replanner=replanner,
        environment=environment,
    )
