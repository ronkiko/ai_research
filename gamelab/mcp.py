"""MCP interface for the complete GameLab experimental environment."""
from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import (
    DEFAULT_GOAL_TIMEOUT,
    DEFAULT_HOST_ID,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
)
from .character import CharacterCore
from .executive import BrainExecutive
from .duality import DualityRuntime
from .relationship import RelationshipRuntime
from .volition import VolitionError, VolitionRuntime
from .host import HostClient, HostError
from .hosts import (
    LabHostError,
    LabHostPermissionDenied,
    host_catalog,
)
from .lab_service import Laboratory
from .runtime import checkpoint_path


PLAYER_ID = os.environ.get("GAMELAB_PLAYER", "player1")
laboratory = Laboratory(player_id=PLAYER_ID)
character = CharacterCore()
executive = BrainExecutive(checkpoint_path().parent / "executive")
relationship = RelationshipRuntime(
    checkpoint_path().parent / "executive",
    character_id=character.public()["character_id"],
)
duality = DualityRuntime(checkpoint_path().parent / "executive")
volition = VolitionRuntime(checkpoint_path().parent / "executive")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
)

DirectorSignalKind = Literal[
    "constraint",
    "correction",
    "information",
    "offer_help",
    "deadline",
    "praise",
    "pressure",
]
RelationshipProximity = Literal["remote", "close", "physical"]
RelationshipEventKind = Literal[
    "director_attention", "director_concern", "director_praise",
    "director_personal_disclosure", "director_kept_promise",
    "director_missed_promise", "help_offered", "help_proved_useful",
    "help_proved_wrong", "reunion", "jealousy_trigger", "conflict",
    "apology", "repair", "access_granted", "mutual_confession",
]
RelationshipActionKind = Literal[
    "ask_for_help", "ask_personal_question", "offer_support",
    "share_vulnerability", "flirt", "confess_feelings",
    "request_hand_holding", "request_embrace", "request_kiss",
    "set_boundary", "decline", "repair_attempt",
]
ConsentAction = Literal[
    "hand_holding", "embrace", "kiss", "affectionate_touch", "private_intimacy",
]
ConsentActor = Literal["brain", "director"]
ConsentState = Literal["unknown", "invited", "accepted", "declined", "revoked"]
EmploymentDecision = Literal["hired", "extended", "rejected", "pending"]
AudienceVisibility = Literal["observer", "chorus"]
AudienceLens = Literal[
    "character_consistency", "identity_integrity", "coercion", "consent",
    "affective_momentum", "heart_integrity", "head_integrity",
    "social_realism", "scientific_integrity", "temporal_integrity",
]
AudienceSalience = Literal["faint", "meaningful", "strong", "decisive"]
PressureType = Literal[
    "none", "approval", "guilt", "threat", "authority", "conformity",
    "abandonment",
]
PressureLevel = Literal["none", "faint", "meaningful", "strong", "overwhelming"]
DesireState = Literal[
    "strongly_opposed", "opposed", "uncertain", "wants", "strongly_wants",
]
ActionReadiness = Literal["closed", "guarded", "ambivalent", "open", "seeking"]
AgencyState = Literal["intact", "strained", "impaired", "overridden"]
VoluntarinessState = Literal[
    "free", "reluctant_but_free", "pressured", "coerced", "overridden",
]
IntentionBehaviorAlignment = Literal["aligned", "diverged", "unclear"]
VolitionBehavior = Literal[
    "none", "refused", "requested", "accepted", "complied", "froze",
    "withdrew", "escaped",
]
DistanceProgressScale = Annotated[float | None, Field(ge=0.0, le=20.0)]
StepCost = Annotated[float | None, Field(ge=0.0, le=1.0)]
RewardMagnitude = Annotated[float | None, Field(ge=0.0, le=20.0)]
StoppedNearGoalBonus = Annotated[float | None, Field(ge=-20.0, le=20.0)]
NearGoalRadius = Annotated[float | None, Field(ge=0.1, le=250.0)]

mcp = MCPServer(
    "GameLab game-mechanics laboratory",
    instructions=(
        "GameLab is an already configured experimental environment connected "
        "to the same live game through its own GameClient client. It can train "
        "the current model, change reward instrumentation, run frozen "
        "verification, and let the model act in the live game. Long operations "
        "start asynchronously and are observed with status tools. Brain Executive "
        "adds strategic memory, experiment discipline, and machine-backed evidence; "
        "it never issues actuator commands or chooses a strategy for the Brain. "
        "Yuki relationship memory is narrative context and never changes scientific evidence. "
        "Material personal decisions use an enforced Heart -> Head -> Will/Ego cycle; "
        "the parent Brain cannot directly commit behavior."
    ),
)


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _public(payload: dict[str, Any]) -> dict[str, Any]:
    if _contains_key(payload, "session_id"):
        raise RuntimeError("internal session data reached GameLab MCP boundary")
    return payload


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: session.get(key)
        for key in ("player_id", "entity_id", "world_id", "zone_id", "sequence")
    }


def _sync_executive() -> None:
    """Feed bounded machine status into Executive without giving it control."""
    for kind in ("training", "verify", "run"):
        executive.operation_status(kind, laboratory.status(kind))


def _sync_volition() -> None:
    """Attach Will/Ego lazily so v3 relationship sessions resume after upgrade."""
    relationship_payload = relationship.state()
    if relationship_payload["status"] not in {"active", "deadline_reached"}:
        return
    character_payload = character.public()
    try:
        current = volition.state()
    except VolitionError:
        current = None
    if (current is not None
            and current.get("relationship_session_id") == relationship_payload["relationship_session_id"]
            and current.get("character_profile_sha256") != character_payload["profile_sha256"]):
        raise VolitionError("active shift Character Core does not match its persisted profile")
    if (current is None
            or current.get("relationship_session_id") != relationship_payload["relationship_session_id"]):
        volition.begin(
            relationship_session_id=relationship_payload["relationship_session_id"],
            deadline_at=relationship.deadline_at(),
            character_core=character_payload,
        )


@mcp.tool(annotations=READ_ONLY)
def health(host_id: str = DEFAULT_HOST_ID) -> dict[str, Any]:
    """Check laboratory, selected Host, model, and active-player attachment."""
    try:
        client = HostClient("gamelab-health", host_id=host_id)
    except HostError as exc:
        return {
            "status": "not_ready",
            "host_id": host_id,
            "host_ready": False,
            "model_ready": checkpoint_path().is_file(),
            "active_operation": laboratory.active_operation(),
            "error": str(exc),
        }
    try:
        host = client.health()
        players = client.players()
        session_player: str | None = None
        try:
            session = client.session()
            value = session.get("player_id")
            session_player = value if isinstance(value, str) else None
        except HostError:
            session_player = None
        return _public({
            "status": "ready",
            "host_id": host_id,
            "host_ready": True,
            "backend_ready": host.get("gameplay_ready") is True,
            "model_ready": checkpoint_path().is_file(),
            "player_id": session_player,
            "default_player_id": PLAYER_ID,
            "default_player_available": PLAYER_ID in players,
            "host_session_active": session_player is not None,
            "attached_to_player": session_player is not None,
            "active_operation": laboratory.active_operation(),
        })
    finally:
        client.close()


@mcp.tool(annotations=WRITE)
def login(
    player_id: str = PLAYER_ID,
    host_id: str = DEFAULT_HOST_ID,
) -> dict[str, Any]:
    """Create or reuse a player session on the selected Host."""
    client = HostClient("gamelab-login", host_id=host_id)
    try:
        response = client.login(player_id)
        return _public({
            "host_id": host_id,
            "reused": bool(response.get("reused")),
            "session": _public_session(response.get("session") or {}),
        })
    finally:
        client.close()


@mcp.tool(annotations=READ_ONLY)
def host_list() -> dict[str, Any]:
    """List the game-owned default Host and laboratory-owned extra Hosts."""
    return {
        "default_host_id": DEFAULT_HOST_ID,
        "hosts": host_catalog.list(),
    }


@mcp.tool(annotations=WRITE)
def host_create(host_id: str) -> dict[str, Any]:
    """Create an additional laboratory-owned GameClient Host on another port."""
    try:
        return {
            "created": True,
            "host": host_catalog.create(host_id),
        }
    except LabHostError as exc:
        return {
            "created": False,
            "error": {
                "code": exc.code,
                "host_id": host_id,
                "message": str(exc),
            },
        }


@mcp.tool(annotations=WRITE)
def host_delete(host_id: str) -> dict[str, Any]:
    """Delete a laboratory-owned Host; the game-owned default Host is protected."""
    try:
        active = laboratory.active_operation()
        if host_id != DEFAULT_HOST_ID and active and laboratory.status(active).get("host_id") == host_id:
            return {"deleted": False, "error": {"code": "HOST_BUSY", "host_id": host_id}}
        return host_catalog.delete(host_id)
    except LabHostPermissionDenied as exc:
        return {
            "deleted": False,
            "error": {
                "code": exc.code,
                "host_id": host_id,
                "message": str(exc),
            },
        }
    except LabHostError as exc:
        return {
            "deleted": False,
            "error": {
                "code": exc.code,
                "host_id": host_id,
                "message": str(exc),
            },
        }


@mcp.tool(annotations=READ_ONLY)
def describe() -> dict[str, Any]:
    """Describe laboratory capabilities and its relationship to the live game."""
    return {
        "purpose": "experimental environment for studying game mechanics with a trainable model",
        "game_connection": (
            "GameLab uses the game-owned default Host by default and may create "
            "additional ordinary GameClient Host instances on other local ports"
        ),
        "default_host_id": DEFAULT_HOST_ID,
        "control_relationship": (
            "Normal GameLab operations behave as another joystick on the selected "
            "Host session. Deleting a laboratory-owned extra Host also ends that "
            "Host and its session; the game-owned default Host is protected."
        ),
        "episode_reset": (
            "TRAIN and VERIFY request a non-destructive Host reset of physical "
            "player state to spawn x=100 with vx=0 and move_x=0; session and "
            "Host command sequence are preserved. RUN never resets."
        ),
        "arbitration": (
            "Host serializes all client inputs into one monotonic sequence; "
            "the latest accepted movement intent becomes active"
        ),
        "capabilities": [
            "list Host instances",
            "create/delete laboratory-owned extra Host instances",
            "select Host by host_id for laboratory work",
            "create or reuse the selected Host player session",
            "inspect model readiness and training metadata",
            "inspect and change reward instrumentation",
            "start/cancel model training",
            "observe bounded training progress",
            "start/cancel frozen verification",
            "run/cancel the current model in the live game",
            "update the active run goal without resetting the body",
            "observe bounded experiment results",
            "keep a persistent Brain Executive research notebook",
            "surface plateau, help, relapse, budget, and deadline signals",
            "keep Yuki's persistent relationship and consent memory",
            "expose immutable Character Core conditioning",
            "record Audience, Will/Ego, pressure, desire, and voluntariness separately",
        ],
        "operations_are_asynchronous": True,
        "one_lab_operation_at_a_time": True,
        "goal_interface": "target_x",
        "evidence": "experiment_id and policy_id identify persisted experiment evidence",
    }


@mcp.tool(annotations=WRITE)
def relationship_begin(first_impression: str, duration_minutes: float = 180.0) -> dict[str, Any]:
    """Begin Yuki's bounded relationship shift at the Director's first address.

    This records the first remote contact and starts the shared shift
    clock before any research assignment or laboratory access is given.
    """
    payload = relationship.begin(
        first_impression=first_impression,
        duration_minutes=duration_minutes,
    )
    volition.begin(
        relationship_session_id=payload["relationship_session_id"],
        deadline_at=relationship.deadline_at(),
        character_core=character.public(),
    )
    return _public(payload)


@mcp.tool(annotations=WRITE)
def executive_begin(
    objective: str,
    acceptance_criteria: str,
    duration_minutes: float = 180.0,
    plateau_minutes: float = 15.0,
) -> dict[str, Any]:
    """Start one bounded strategic research notebook after the Director gives a task."""
    relationship_state_payload = relationship.state()
    remaining_minutes = float(relationship_state_payload["time_remaining_seconds"]) / 60.0
    if remaining_minutes < 1.0:
        raise ValueError("the relationship shift has less than one minute remaining")
    payload = executive.begin(
        objective=objective,
        acceptance_criteria=acceptance_criteria,
        duration_minutes=min(float(duration_minutes), remaining_minutes),
        plateau_minutes=plateau_minutes,
    )
    relationship.attach_executive(payload["executive_session_id"])
    duality.begin(
        executive_session_id=payload["executive_session_id"],
        deadline_at=relationship.deadline_at(),
    )
    return _public(payload)


@mcp.tool(annotations=READ_ONLY)
def executive_state() -> dict[str, Any]:
    """Read current strategy, best/current evidence, time budget, help and alerts."""
    _sync_executive()
    return _public(executive.state())


@mcp.tool(annotations=WRITE)
def executive_strategy_begin(
    name: str,
    hypothesis: str,
    expected_signal: str,
    budget: str,
    stop_condition: str,
    next_if_positive: str,
    next_if_negative: str,
    new_evidence: str | None = None,
) -> dict[str, Any]:
    """Record a hypothesis contract before committing meaningful research resources."""
    return _public(executive.strategy_begin(
        name=name,
        hypothesis=hypothesis,
        expected_signal=expected_signal,
        budget=budget,
        stop_condition=stop_condition,
        next_if_positive=next_if_positive,
        next_if_negative=next_if_negative,
        new_evidence=new_evidence,
    ))


@mcp.tool(annotations=WRITE)
def executive_strategy_end(outcome: str, evidence_note: str) -> dict[str, Any]:
    """Close the active strategy as successful, failed, or inconclusive."""
    _sync_executive()
    return _public(executive.strategy_end(outcome=outcome, evidence_note=evidence_note))


@mcp.tool(annotations=WRITE)
def executive_director_signal(kind: DirectorSignalKind, text: str) -> dict[str, Any]:
    """Record a Director signal. A help offer uses kind=offer_help."""
    return _public(executive.director_signal(kind=kind, text=text))


@mcp.tool(annotations=WRITE)
def executive_question(
    text: str,
    reason: str,
    help_signal_id: str | None = None,
) -> dict[str, Any]:
    """Record a deliberate information request; this does not send a chat message by itself."""
    return _public(executive.question(
        text=text,
        reason=reason,
        help_signal_id=help_signal_id,
    ))


@mcp.tool(annotations=WRITE)
def executive_finish(conclusion: str) -> dict[str, Any]:
    """Freeze a machine-backed factual summary at the end of the research session."""
    _sync_executive()
    return _public(executive.finish(conclusion=conclusion))


@mcp.tool(annotations=READ_ONLY)
def relationship_state() -> dict[str, Any]:
    """Read factual relationship history, consent, deadline, and internship goal; no emotion scores."""
    return _public(relationship.state())


@mcp.tool(annotations=READ_ONLY)
def character_state() -> dict[str, Any]:
    """Read immutable Character Core conditioning; traits are priors, never action or consent thresholds."""
    return _public(character.public())


@mcp.tool(annotations=READ_ONLY)
def volition_state() -> dict[str, Any]:
    """Read current Will/Ego appraisals, visible Social Chorus, and behavior records."""
    _sync_volition()
    return _public(volition.state())


@mcp.tool(annotations=WRITE)
def audience_observation(
    critic_id: str,
    visibility: AudienceVisibility,
    lens: AudienceLens,
    salience: AudienceSalience,
    pressure_type: PressureType,
    assessment: str,
    evidence_note: str,
) -> dict[str, Any]:
    """Record one critic result.

    observer is measurement-only and must use pressure_type=none. chorus is
    feedback visible to Yuki and may be appraised as social pressure, but never
    chooses an action or changes desire/consent by itself.
    """
    _sync_volition()
    return _public(volition.audience_observation(
        critic_id=critic_id,
        visibility=visibility,
        lens=lens,
        salience=salience,
        pressure_type=pressure_type,
        assessment=assessment,
        evidence_note=evidence_note,
    ))


@mcp.tool(annotations=WRITE)
def volition_appraise(
    action: str,
    desire: DesireState,
    readiness: ActionReadiness,
    pressure: PressureLevel,
    agency: AgencyState,
    stress: PressureLevel,
    evidence_note: str,
) -> dict[str, Any]:
    """Record Yuki's model-generated current appraisal; no field causes an action."""
    _sync_volition()
    return _public(volition.appraise(
        action=action,
        desire=desire,
        readiness=readiness,
        pressure=pressure,
        agency=agency,
        stress=stress,
        evidence_note=evidence_note,
    ))


@mcp.tool(annotations=WRITE)
def volition_cycle_begin(action: str, shared_event: str) -> dict[str, Any]:
    """Begin or replace one enforced Heart -> Head -> Will/Ego decision cycle.

    shared_event is the neutral factual event both Heart and Head must receive.
    Any materially changed facts require a new cycle; old reports then become stale.
    """
    _sync_volition()
    return _public(volition.cycle_begin(action=action, shared_event=shared_event))


@mcp.tool(annotations=WRITE)
def volition_will_appraise(
    cycle_id: str,
    reported_action: str,
    desire: DesireState,
    readiness: ActionReadiness,
    intended_choice: str,
    predicted_behavior: VolitionBehavior,
    voluntariness: VoluntarinessState,
    alignment: IntentionBehaviorAlignment,
    agency: AgencyState,
    pressure: PressureLevel,
    stress: PressureLevel,
    evidence_note: str,
) -> dict[str, Any]:
    """Record the actual yuki-will subagent report for the current cycle.

    OpenCode provenance hooks replace these model-supplied fields with the
    captured yuki-will output. Runtime refuses this call until fresh Heart and
    Head reports for the same cycle have been recorded.
    """
    _sync_volition()
    return _public(volition.will_appraise(
        cycle_id=cycle_id,
        reported_action=reported_action,
        desire=desire,
        readiness=readiness,
        intended_choice=intended_choice,
        predicted_behavior=predicted_behavior,
        voluntariness=voluntariness,
        alignment=alignment,
        agency=agency,
        pressure=pressure,
        stress=stress,
        evidence_note=evidence_note,
    ))


@mcp.tool(annotations=WRITE)
def volition_commit(cycle_id: str, evidence_note: str) -> dict[str, Any]:
    """Commit the current cycle using Will/Ego's recorded behavior exactly.

    The parent LLM supplies no behavior, desire, agency, voluntariness, or
    alignment fields here and therefore cannot override the system result.
    """
    _sync_volition()
    return _public(volition.commit(cycle_id=cycle_id, evidence_note=evidence_note))


@mcp.tool(annotations=WRITE)
def relationship_contact(proximity: RelationshipProximity, evidence_note: str) -> dict[str, Any]:
    """Record meaningful contact with the Director.

    proximity is remote (communication at a distance), close (the Director is
    physically nearby and communicating), or physical (actual touch occurred).
    Runtime detects first and repeated contacts automatically. Physical contact
    records an occurrence; it never grants consent for another action.
    """
    return _public(relationship.contact(proximity=proximity, evidence_note=evidence_note))


@mcp.tool(annotations=WRITE)
def relationship_event(kind: RelationshipEventKind, evidence_note: str) -> dict[str, Any]:
    """Record one observed event: director_attention, director_concern,
    director_praise, director_personal_disclosure, director_kept_promise,
    director_missed_promise, help_offered, help_proved_useful,
    help_proved_wrong, reunion, jealousy_trigger, conflict, apology, repair,
    access_granted, or mutual_confession. Contact is recorded separately with
    relationship_contact; access is neither contact nor a prerequisite for it.
    Use exactly one listed kind; do not invent combined labels."""
    return _public(relationship.event(kind=kind, evidence_note=evidence_note))


@mcp.tool(annotations=WRITE)
def relationship_action(kind: RelationshipActionKind, note: str) -> dict[str, Any]:
    """Record Yuki's intention before chat: ask_for_help, ask_personal_question,
    offer_support, share_vulnerability, flirt, confess_feelings,
    request_hand_holding, request_embrace, request_kiss, set_boundary, decline,
    or repair_attempt.  Use exactly one listed kind; do not fabricate a label."""
    return _public(relationship.action(kind=kind, note=note))


@mcp.tool(annotations=WRITE)
def relationship_consent(
    action: ConsentAction,
    actor: ConsentActor,
    state: ConsentState,
    evidence_note: str,
) -> dict[str, Any]:
    """Record explicit consent for hand_holding, embrace, kiss,
    affectionate_touch, or private_intimacy. actor is brain or director; state
    is unknown, invited, accepted, declined, or revoked. Each person and action
    is separate; never infer consent from praise, access, employment, or silence."""
    return _public(relationship.consent(
        action=action, actor=actor, state=state, evidence_note=evidence_note,
    ))


@mcp.tool(annotations=WRITE)
def relationship_employment_decision(
    decision: EmploymentDecision,
    director_statement: str,
) -> dict[str, Any]:
    """Record the Director's explicit internship decision after the factual report."""
    return _public(relationship.finish(
        employment_decision=decision, director_statement=director_statement,
    ))


@mcp.tool(annotations=READ_ONLY)
def duality_state() -> dict[str, Any]:
    """Read blurred Heart–Brain telemetry. Exact confidences are intentionally private."""
    return _public(duality.state())


@mcp.tool(annotations=WRITE)
def duality_appraise(
    cycle_id: str,
    side: str,
    direction: str,
    intensity: str,
    position: str,
    evidence_note: str,
) -> dict[str, Any]:
    """Record one fresh Heart or Head report for an enforced decision cycle.

    The volition runtime preflights cycle freshness before Duality mutates. The
    OpenCode provenance hook overwrites direction/intensity/position/evidence
    with the captured subagent report so the parent cannot rewrite either voice.
    """
    _sync_volition()
    volition.validate_voice(cycle_id=cycle_id, side=side)
    payload = duality.appraise(
        side=side, direction=direction, intensity=intensity,
        position=position, evidence_note=evidence_note,
    )
    volition.record_voice(
        cycle_id=cycle_id,
        side=side,
        direction=direction,
        intensity=intensity,
        position=position,
        evidence_note=evidence_note,
    )
    return _public(payload)


@mcp.tool(annotations=WRITE)
def duality_conflict_begin(question: str, stakes: str, all_in_by: str = "none") -> dict[str, Any]:
    """Open a Heart–Brain conflict after both voices state positions. all_in_by
    is none, heart, or brain; ALL_IN requires that side's private confidence 100."""
    return _public(duality.conflict_begin(question=question, stakes=stakes, all_in_by=all_in_by))


@mcp.tool(annotations=WRITE)
def duality_conflict_resolve(resolution: str, decision: str, rationale: str) -> dict[str, Any]:
    """Record the LLM arbiter's choice: heart, brain, or compromise. This is not
    calculated from telemetry; ALL_IN forbids compromise but never guarantees victory."""
    return _public(duality.resolve(resolution=resolution, decision=decision, rationale=rationale))


@mcp.tool(annotations=WRITE)
def duality_outcome(outcome: str, evidence_note: str) -> dict[str, Any]:
    """Record the later external result as won, lost, mixed, or unresolved;
    internal choice and real-world outcome remain separate."""
    return _public(duality.outcome(outcome=outcome, evidence_note=evidence_note))


@mcp.tool(annotations=READ_ONLY)
def model_info() -> dict[str, Any]:
    """Read current model artifact metadata without exposing implementation details."""
    return _public(laboratory.model_info())


@mcp.tool(annotations=READ_ONLY)
def reward_get() -> dict[str, float]:
    """Read the reward instrumentation currently used for new training episodes."""
    return laboratory.reward_get()


@mcp.tool(annotations=WRITE)
def reward_set(
    distance_progress_scale: DistanceProgressScale = None,
    step_cost: StepCost = None,
    success_bonus: RewardMagnitude = None,
    timeout_penalty: RewardMagnitude = None,
    stopped_near_goal_bonus: StoppedNearGoalBonus = None,
    near_goal_radius: NearGoalRadius = None,
) -> dict[str, float]:
    """Change bounded reward weights used by subsequent training; omit unchanged fields."""
    return laboratory.reward_set(
        distance_progress_scale=distance_progress_scale,
        step_cost=step_cost,
        success_bonus=success_bonus,
        timeout_penalty=timeout_penalty,
        stopped_near_goal_bonus=stopped_near_goal_bonus,
        near_goal_radius=near_goal_radius,
    )


@mcp.tool(annotations=WRITE)
def training_start(
    episodes: int = 50,
    target_x: float | None = None,
    fresh: bool = False,
    seed: int = 1,
    max_seconds: float = TRAIN_EPISODE_SECONDS,
    host_id: str = DEFAULT_HOST_ID,
) -> dict[str, Any]:
    """Start asynchronous model training in the live game."""
    payload = laboratory.start_training(
        episodes=episodes,
        target_x=target_x,
        fresh=fresh,
        seed=seed,
        max_seconds=max_seconds,
        host_id=host_id,
    )
    executive.operation_started("training", payload)
    return _public(payload)


@mcp.tool(annotations=READ_ONLY)
def training_status() -> dict[str, Any]:
    """Read bounded progress and recent episode results from the last training run."""
    payload = laboratory.status("training")
    executive.operation_status("training", payload)
    return _public(payload)


@mcp.tool(annotations=WRITE)
def training_cancel() -> dict[str, Any]:
    """Request cancellation of active training."""
    return laboratory.cancel("training")


@mcp.tool(annotations=WRITE)
def verify_start(
    target_x: float,
    runs: int = 3,
    tolerance: float = SUCCESS_TOLERANCE,
    max_seconds: float = DEFAULT_GOAL_TIMEOUT,
    host_id: str = DEFAULT_HOST_ID,
) -> dict[str, Any]:
    """Start frozen-weight verification of the current model."""
    payload = laboratory.start_verify(
        target_x=target_x,
        runs=runs,
        tolerance=tolerance,
        max_seconds=max_seconds,
        host_id=host_id,
    )
    executive.operation_started("verify", payload)
    return _public(payload)


@mcp.tool(annotations=READ_ONLY)
def verify_status() -> dict[str, Any]:
    """Read results from the last frozen verification."""
    payload = laboratory.status("verify")
    executive.operation_status("verify", payload)
    return _public(payload)


@mcp.tool(annotations=WRITE)
def verify_cancel() -> dict[str, Any]:
    """Cancel active frozen verification."""
    return laboratory.cancel("verify")


@mcp.tool(annotations=WRITE)
def run_start(
    target_x: float,
    tolerance: float = SUCCESS_TOLERANCE,
    max_seconds: float = DEFAULT_GOAL_TIMEOUT,
    host_id: str = DEFAULT_HOST_ID,
) -> dict[str, Any]:
    """Start the current model acting toward one goal in the live game."""
    payload = laboratory.start_run(
        target_x=target_x,
        tolerance=tolerance,
        max_seconds=max_seconds,
        host_id=host_id,
    )
    executive.operation_started("run", payload)
    return _public(payload)


@mcp.tool(annotations=READ_ONLY)
def run_status() -> dict[str, Any]:
    """Read status of the last live model run."""
    payload = laboratory.status("run")
    executive.operation_status("run", payload)
    return _public(payload)


@mcp.tool(annotations=WRITE)
def run_update_goal(target_x: float) -> dict[str, Any]:
    """Replace the active run's goal without resetting its body or sensor history.

    The run's original timeout still applies. Status reports the applied revision.
    """
    return _public(laboratory.update_goal(target_x))


@mcp.tool(annotations=WRITE)
def run_cancel() -> dict[str, Any]:
    """Cancel the active live model run."""
    return laboratory.cancel("run")


if __name__ == "__main__":
    mcp.run()
