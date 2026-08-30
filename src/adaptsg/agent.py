"""Bounded LangGraph orchestration and the public AdaptSG service facade."""

from __future__ import annotations

from datetime import date
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from adaptsg.domain import (
    Itinerary,
    ParseOutcome,
    PlanOutcome,
    ReplanProposal,
    ReplanTrigger,
)
from adaptsg.errors import NoFeasibleItinerary
from adaptsg.planning import JourneyPlanner, JourneyReplanner
from adaptsg.preference_parser import BedrockPreferenceParser, PreferenceParser
from adaptsg.settings import Settings, get_settings
from adaptsg.tools.catalog import VenueCatalog
from adaptsg.tools.routing import DemoRoutingClient
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
    ) -> None:
        self.parser = parser
        self.planner = planner
        self.replanner = replanner
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


def build_service(settings: Settings | None = None) -> AdaptSGService:
    resolved = settings or get_settings()
    catalog = VenueCatalog()
    validator = ItineraryValidator(max_replans=resolved.adaptsg_max_replans)
    planner = JourneyPlanner(
        catalog=catalog,
        routing=DemoRoutingClient(),
        validator=validator,
    )
    replanner = JourneyReplanner(
        planner=planner,
        approval_cost_increase_sgd=resolved.adaptsg_approval_cost_increase_sgd,
        max_replans=resolved.adaptsg_max_replans,
    )
    parser = BedrockPreferenceParser(settings=resolved, catalog=catalog)
    return AdaptSGService(parser=parser, planner=planner, replanner=replanner)
