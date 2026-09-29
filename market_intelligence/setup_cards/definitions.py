"""Setup definitions and the deterministic setup-evaluation payload.

**No live setup definition exists.** ``REGISTERED_SETUP_DEFINITIONS`` is
empty, so in production no setup card can be built and no qualification can
be cited: production qualification remains unauthorized. Tests pass
synthetic definitions explicitly; nothing here implies a live definition or
qualification authority exists.

``setup_evaluation.v1`` is the payload a registered setup-definition producer
would emit as a ``deterministic_calculation``. It is the only citable source
of a card's lifecycle, qualification and expiry, so those values are never
uncited builder inputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator

from market_intelligence.evidence.contracts import SUBJECT_PATTERNS
from market_intelligence.evidence.enums import SubjectType
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    ProducerId,
    UtcTimestamp,
    VersionLabel,
)
from market_intelligence.setup_cards.enums import (
    CONCLUDED_QUALIFICATIONS,
    LANE_CONCLUSION_LIFECYCLES,
    PRE_EVALUATION_LIFECYCLES,
    Lane,
    NoSetupReason,
    SetupLifecycle,
    SetupQualification,
)

SETUP_EVALUATION_PAYLOAD = "setup_evaluation.v1"


class SetupDefinition(BaseModel):
    """A reviewed, registered setup definition (none exists today).

    Only the VWAP-reversion lane can have one: no trend-day research or
    definition exists. Inference is never an input in this version.
    """

    model_config = STRICT_FROZEN

    setup_definition_id: VersionLabel
    lane: Literal[Lane.VWAP_REVERSION]
    evaluation_producer_id: ProducerId
    evaluation_payload_schema_id: Literal["setup_evaluation.v1"] = SETUP_EVALUATION_PAYLOAD
    inference_allowed: Literal[False] = False


# Production table: deliberately empty. Adding an entry needs its own
# separately reviewed research and authorization.
REGISTERED_SETUP_DEFINITIONS: tuple[SetupDefinition, ...] = ()


def lifecycle_qualification_compatible(
    lifecycle: SetupLifecycle | None, qualification: SetupQualification
) -> bool:
    """Lifecycle never implies qualification, but they must be consistent:
    observed/developing -> not_evaluated; evaluation_complete -> a concluded
    qualification; terminal states keep whatever qualification existed."""
    if lifecycle in PRE_EVALUATION_LIFECYCLES:
        return qualification is SetupQualification.NOT_EVALUATED
    if lifecycle is SetupLifecycle.EVALUATION_COMPLETE:
        return qualification in CONCLUDED_QUALIFICATIONS
    return True


class SetupEvaluationPayload(BaseModel):
    """``setup_evaluation.v1`` (offline contract only): one deterministic
    evaluation by a registered setup definition.

    - **Setup-level** (``setup_subject_id`` set): one candidate's lifecycle
      and qualification; ``no_setup_reason`` is null.
    - **Lane-level** (``setup_subject_id`` null): the lane's conclusion that
      nothing qualified. It must be ``not_qualified`` (never
      ``not_evaluated``), at ``evaluation_complete`` or a later terminal
      state, and state which card reason it supports.
    """

    model_config = STRICT_FROZEN

    setup_definition_id: VersionLabel
    lane: Lane
    setup_subject_id: str | None = None
    lifecycle_state: SetupLifecycle
    qualification: SetupQualification
    no_setup_reason: NoSetupReason | None = None
    expires_at_utc: UtcTimestamp | None = None

    @model_validator(mode="after")
    def _check_payload(self) -> SetupEvaluationPayload:
        if not lifecycle_qualification_compatible(self.lifecycle_state, self.qualification):
            raise ValueError("lifecycle and qualification are incompatible")
        if self.setup_subject_id is not None:
            if not SUBJECT_PATTERNS[SubjectType.SETUP_CANDIDATE].fullmatch(self.setup_subject_id):
                raise ValueError("setup_subject_id must be canonical")
            if self.setup_subject_id.split(":")[1] != self.lane.value:
                raise ValueError("setup subject lane must equal the payload lane")
            if self.no_setup_reason is not None:
                raise ValueError("a setup-level evaluation carries no no-setup reason")
            return self
        if self.qualification is not SetupQualification.NOT_QUALIFIED:
            raise ValueError("a lane-level evaluation must conclude not_qualified")
        if self.lifecycle_state not in LANE_CONCLUSION_LIFECYCLES:
            raise ValueError("a lane-level conclusion needs a completed evaluation")
        if self.no_setup_reason is None:
            raise ValueError("a lane-level conclusion must state its no-setup reason")
        return self


def expires_passed(expires_at: datetime | None, as_of: datetime) -> bool:
    return expires_at is not None and as_of >= expires_at
