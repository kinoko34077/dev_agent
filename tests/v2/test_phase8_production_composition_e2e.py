from __future__ import annotations

import inspect


def test_phase8_production_composition_exposes_only_submission_and_observation_boundary():
    """The E2E driver must not become a shadow orchestrator.

    Phase 8 composition owns Planner -> Worker -> Host Verification -> Reviewer
    -> Integration -> dependency release internally.  A caller/test submits one
    root and observes durable state; it must not sequence those authorities.
    """

    from scripts.devfarm_production_composition import Phase8ProductionComposition

    public = {
        name
        for name, member in inspect.getmembers(Phase8ProductionComposition)
        if callable(member) and not name.startswith("_")
    }

    assert public == {"submit", "observe"}
