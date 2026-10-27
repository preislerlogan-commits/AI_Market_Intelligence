"""The offline evidence assistant: cited, labelled answers from the selected
card's own bundle; missing, stale and conflicting evidence; selector-status
preservation; refusal of outside evidence and mismatched handoffs; the
unsupported-question response; the synthetic-only grant; and isolation.
Synthetic dashboard scenarios only."""

from __future__ import annotations

import ast
import builtins
import dataclasses
import re
import socket
from pathlib import Path

import duckdb
import pytest

from market_intelligence.assistant import explain as A
from market_intelligence.dashboard import labels as L
from market_intelligence.dashboard.adapter import present
from market_intelligence.dashboard.fixtures import build_scenarios
from market_intelligence.dashboard.navigation import (
    NavigationError,
    NavigationState,
    Page,
    assistant_panel,
    open_assistant,
    open_card,
)
from market_intelligence.evidence.enums import (
    BundlePurpose,
    CitationRole,
    ConsumerId,
    ConsumerPermission,
    EvidenceKind,
)
from market_intelligence.evidence.registry import ConsumerGrant
from market_intelligence.evidence.validation import validate_citations

PACKAGE = Path(A.__file__).parent
REJECTED_CONTRACT = "SPY270112C00682000"
_ASSISTANT_GRANT = re.compile(r"ConsumerGrant\(\s*consumer_id=ConsumerId\.ASSISTANT")
KIND_OF = {
    A.StatementLabel.FACT: EvidenceKind.CONFIRMED_FACT,
    A.StatementLabel.CALCULATION: EvidenceKind.DETERMINISTIC_CALCULATION,
    A.StatementLabel.RESEARCH_RESULT: EvidenceKind.HISTORICAL_RESEARCH_RESULT,
    A.StatementLabel.INFERENCE: EvidenceKind.CURRENT_INFERENCE,
}


def _selection(scenario_id: str, *, revision: int = -1, **changes) -> A.Selection:
    scenario = build_scenarios()[scenario_id]
    record = scenario.vwap.history[revision]
    fields = dict(
        card=record.card,
        bundle=record.source.bundle,
        items=record.source.items,
        conflicts=record.source.conflicts,
        registry=scenario.registry,
    )
    fields.update(changes)
    return A.Selection(**fields)


def _ask(selection: A.Selection, question, **ids) -> A.Answer:
    return A.answer(
        selection,
        question.value if isinstance(question, A.QuestionId) else question,
        card_id=ids.get("card_id", selection.card.card_id),
        bundle_id=ids.get("bundle_id", selection.bundle.bundle_id),
    )


def _refusal(selection: A.Selection, question=A.QuestionId.QUALIFICATION, **ids) -> str:
    with pytest.raises(A.AssistantRefusal) as excinfo:
        _ask(selection, question, **ids)
    return excinfo.value.reason


def _texts(answer: A.Answer) -> str:
    return " ".join([answer.headline, *(s.text for s in (*answer.statements, *answer.inference))])


# --- Cited, labelled answers --------------------------------------------------------------------


def test_every_supported_question_is_answered_with_cited_labelled_statements():
    selection = _selection("ready_for_manual_review")
    entries = {e.item_id: e for e in selection.bundle.entries}
    for question in A.DETERMINISTIC_QUESTIONS:
        answer = _ask(selection, question)
        assert answer.answerable, question
        assert (answer.card_id, answer.bundle_id) == (
            selection.card.card_id,
            selection.bundle.bundle_id,
        )
        # Nothing is missing or stale here, which is itself the answer.
        assert answer.statements or question is A.QuestionId.MISSING_OR_STALE, question
        citations = []
        for statement in (*answer.statements, *answer.inference):
            assert statement.reference_type is A.ReferenceType.EVIDENCE_ITEM
            citation = statement.evidence_citation
            assert citation is not None and citation.bundle_id == selection.bundle.bundle_id
            assert set(citation.cited_item_ids) <= set(entries)
            if statement.label in KIND_OF:
                assert citation.cited_kinds == [KIND_OF[statement.label]]
            citations.append(citation)
        validate_citations(selection.bundle, citations)
        assert L.forbidden_terms_in(_texts(answer)) == []


def test_inference_is_kept_apart_from_supporting_and_contradicting_evidence():
    selection = _selection("ready_for_manual_review")
    for question in (A.QuestionId.SUPPORTING, A.QuestionId.CONTRADICTING):
        answer = _ask(selection, question)
        assert answer.inference and all(
            s.label is A.StatementLabel.INFERENCE for s in answer.inference
        )
        assert all(s.label is not A.StatementLabel.INFERENCE for s in answer.statements)
    supporting = _ask(selection, A.QuestionId.SUPPORTING).statements
    assert {s.evidence_citation.citation_role for s in supporting} == {CitationRole.SUPPORTS_CLAIM}


# --- Missing, stale and conflicting evidence ----------------------------------------------------


def test_missing_required_evidence_is_cited_by_the_bundle_record_never_an_invented_item():
    selection = _selection("missing_evidence")
    answer = _ask(selection, A.QuestionId.MISSING_OR_STALE)
    (statement,) = answer.statements
    assert statement.label is A.StatementLabel.MISSING_EVIDENCE
    assert statement.reference_type is A.ReferenceType.MISSING_REQUIREMENT
    assert statement.evidence_citation is None
    record = statement.missing_requirement
    assert record.bundle_id == selection.bundle.bundle_id
    assert record.requirement_id == "req.bars.spy"
    assert record.requirement_id in {
        m.requirement_id for m in selection.bundle.missing_required_producers
    }
    qualification = _ask(selection, A.QuestionId.QUALIFICATION)
    assert "missing required evidence" in qualification.headline
    supporting = _ask(selection, A.QuestionId.SUPPORTING)
    assert not supporting.answerable and supporting.headline.startswith(A.NOT_ANSWERABLE)


def test_reference_types_cannot_be_swapped():
    statement = _ask(_selection("missing_evidence"), A.QuestionId.MISSING_OR_STALE).statements[0]
    requirement = statement.missing_requirement
    citation = (
        _ask(_selection("ready_for_manual_review"), A.QuestionId.SUPPORTING)
        .statements[0]
        .evidence_citation
    )
    label = A.StatementLabel.MISSING_EVIDENCE
    swaps = [
        (label, A.ReferenceType.EVIDENCE_ITEM, {"missing_requirement": requirement}),
        (label, A.ReferenceType.MISSING_REQUIREMENT, {"evidence_citation": citation}),
        (
            A.StatementLabel.FACT,
            A.ReferenceType.MISSING_REQUIREMENT,
            {"missing_requirement": requirement},
        ),
        (
            label,
            A.ReferenceType.EVIDENCE_ITEM,
            {"evidence_citation": citation, "missing_requirement": requirement},
        ),
        (label, "evidence_item", {"evidence_citation": citation}),  # an untyped string
    ]
    for statement_label, reference_type, references in swaps:
        with pytest.raises(A.AssistantRefusal, match="reference_type_mismatch"):
            A.Statement(statement_label, "text", reference_type, **references)
    # A requirement ID can never be dressed up as an evidence-item citation.
    with pytest.raises(ValueError):
        citation.model_validate(
            {**citation.model_dump(mode="json"), "cited_item_ids": [requirement.requirement_id]}
        )


def test_forged_references_do_not_resolve_against_the_bundle():
    selection = _selection("missing_evidence")
    statement = _ask(selection, A.QuestionId.MISSING_OR_STALE).statements[0]
    requirement = statement.missing_requirement
    other = _selection("ready_for_manual_review")
    for forged in (
        dataclasses.replace(requirement, requirement_id="req.calc.spy"),  # not missing
        dataclasses.replace(requirement, bundle_id=other.bundle.bundle_id),
        dataclasses.replace(requirement, producer_id="synthetic_session_calc"),
    ):
        forged_statement = dataclasses.replace(statement, missing_requirement=forged)
        with pytest.raises(A.AssistantRefusal, match="missing_requirement_not_in_bundle"):
            A.check_references(selection.bundle, [forged_statement])
    outside = _ask(other, A.QuestionId.SUPPORTING).statements[0]
    with pytest.raises(A.AssistantRefusal, match="citation_outside_bundle"):
        A.check_references(selection.bundle, [outside])


def test_stale_required_evidence_is_labelled_stale_and_cited():
    selection = _selection("stale_required_evidence")
    answer = _ask(selection, A.QuestionId.MISSING_OR_STALE)
    stale = [s for s in answer.statements if s.label is A.StatementLabel.STALE_EVIDENCE]
    required = {n.item_id for n in selection.card.non_current_entries if n.required}
    assert required and required <= {s.evidence_citation.cited_item_ids[0] for s in stale}
    assert all(
        s.evidence_citation.citation_role is CitationRole.STALE_EVIDENCE_NOTICE for s in stale
    )
    assert "stale required evidence" in _ask(selection, A.QuestionId.QUALIFICATION).headline


def test_conflicts_show_both_sides_without_a_winner():
    selection = _selection("unresolved_critical_conflict")
    (note,) = selection.card.conflicts
    for question in (A.QuestionId.QUALIFICATION, A.QuestionId.CONTRADICTING):
        sides = [
            s
            for s in _ask(selection, question).statements
            if s.evidence_citation.citation_role is CitationRole.CONFLICT_NOTICE
        ]
        assert sorted(s.evidence_citation.cited_item_ids[0] for s in sides) == sorted(
            note.involved_item_ids
        )
        assert all(s.evidence_citation.cited_conflict_ids == [note.conflict_id] for s in sides)
        assert all("no winner is chosen" in s.text for s in sides)


# --- Contract status ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario_id", "outcome", "eligible", "research_only"),
    [
        ("ready_for_manual_review", "eligible", 2, 0),
        ("research_only_contracts", "research_only", 0, 2),
        ("no_eligible_contracts", "no_eligible_contracts", 0, 0),
    ],
)
def test_contract_status_repeats_the_selector_exactly(
    scenario_id, outcome, eligible, research_only
):
    selection = _selection(scenario_id)
    section = selection.card.contracts
    answer = _ask(selection, A.QuestionId.CONTRACT_STATUS)
    text = _texts(answer)
    assert f"records outcome `{outcome}`" in text
    for symbol in section.eligible_contracts:
        assert f"`{symbol}`" in answer.statements[1].text
    for symbol in section.research_only_contracts:
        assert f"`{symbol}`" in answer.statements[2].text
    assert len(section.eligible_contracts) == eligible
    assert len(section.research_only_contracts) == research_only
    assert REJECTED_CONTRACT not in text and "rank" not in answer.statements[0].text
    assert all(
        s.evidence_citation.cited_item_ids == [section.selector_result_item_id]
        for s in answer.statements
    )


def test_a_card_that_reclassifies_or_adds_contracts_is_refused():
    selection = _selection("research_only_contracts")
    section = selection.card.contracts
    promoted = section.model_copy(
        update={
            "eligible_contracts": section.research_only_contracts,
            "research_only_contracts": [],
        }
    )
    tampered = selection.card.model_copy(update={"contracts": promoted})
    assert (
        _refusal(dataclasses.replace(selection, card=tampered), A.QuestionId.CONTRACT_STATUS)
        == "selector_outcome_changed"
    )
    added = section.model_copy(
        update={"research_only_contracts": [*section.research_only_contracts, REJECTED_CONTRACT]}
    )
    tampered = selection.card.model_copy(update={"contracts": added})
    assert (
        _refusal(dataclasses.replace(selection, card=tampered), A.QuestionId.CONTRACT_STATUS)
        == "contract_not_returned_by_selector"
    )


def test_no_selector_result_is_not_answerable():
    answer = _ask(_selection("selector_unavailable"), A.QuestionId.CONTRACT_STATUS)
    assert not answer.answerable and answer.statements == ()
    assert answer.headline.startswith(A.NOT_ANSWERABLE)


# --- Bounded retrieval and handoff ---------------------------------------------------------------


def test_evidence_outside_the_selected_bundle_is_refused():
    selection = _selection("ready_for_manual_review")
    other = build_scenarios()["ready_for_manual_review"].market
    outside = next(i for i in other.items if i not in selection.items)
    extra = {**selection.items, outside: other.items[outside]}
    assert _refusal(dataclasses.replace(selection, items=extra)) == "evidence_outside_bundle"
    conflict_scenario = _selection("unresolved_critical_conflict")
    extra_conflicts = {**selection.conflicts, **conflict_scenario.conflicts}
    assert (
        _refusal(dataclasses.replace(selection, conflicts=extra_conflicts))
        == "evidence_outside_bundle"
    )
    fewer = dict(list(selection.items.items())[1:])
    assert _refusal(dataclasses.replace(selection, items=fewer)) == "bundle_evidence_incomplete"


def test_card_bundle_and_handoff_mismatches_are_refused():
    selection = _selection("ready_for_manual_review")
    other = _selection("research_only_contracts")
    assert _refusal(
        dataclasses.replace(selection, card=other.card), card_id=other.card.card_id
    ) == ("card_bundle_mismatch")
    assert _refusal(selection, bundle_id=other.bundle.bundle_id) == "handoff_mismatch"
    assert _refusal(selection, card_id=other.card.card_id) == "handoff_mismatch"


def test_navigation_handoff_carries_the_same_card_and_bundle():
    scenario = build_scenarios()["ready_for_manual_review"]
    model = present(scenario)
    card = scenario.vwap.history[-1].card
    with pytest.raises(NavigationError, match="no_card_selected"):
        open_assistant(NavigationState(scenario_id=scenario.scenario_id))
    state = open_assistant(
        open_card(
            NavigationState(scenario_id=scenario.scenario_id),
            card.card_id,
            card.decision_context_bundle_id,
        )
    )
    assert state.page is Page.ASSISTANT
    panel = assistant_panel(model, state)
    assert (panel.card_id, panel.bundle_id) == (card.card_id, card.decision_context_bundle_id)
    forged = dataclasses.replace(
        model,
        assistant={card.card_id: dataclasses.replace(panel, bundle_id="evb1_" + "0" * 64)},
    )
    with pytest.raises(NavigationError, match="assistant_handoff_mismatch"):
        assistant_panel(forged, state)


def test_unsupported_questions_get_the_safe_not_answerable_response():
    selection = _selection("ready_for_manual_review")
    for question in ("Should I buy this?", "What is the probability it rises?", ""):
        answer = _ask(selection, question)
        assert answer.question_id is None and not answer.answerable
        assert answer.headline.startswith(A.NOT_ANSWERABLE)
        assert answer.statements == () and answer.inference == ()


# --- Grant: synthetic, read-only, setup_detail only ---------------------------------------------


def test_the_assistant_grant_is_synthetic_read_only_and_setup_detail_only():
    registry = build_scenarios()["ready_for_manual_review"].registry
    grant = registry.consumer_grant(ConsumerId.ASSISTANT)
    assert grant.purposes == [BundlePurpose.SETUP_DETAIL]
    assert not grant.machine_decision_mode_allowed
    assert all(p.value.startswith("read_") for p in grant.permissions)
    # Only the dashboard's synthetic fixture registry defines it.
    root = PACKAGE.parent
    defining = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "tests" not in p.parts and _ASSISTANT_GRANT.search(p.read_text("utf-8"))
    )
    assert defining == [str(Path("dashboard") / "fixtures.py")]


def test_registries_without_a_synthetic_read_only_grant_are_refused():
    selection = _selection("ready_for_manual_review")
    registry = selection.registry
    no_grant = registry.model_copy(
        update={
            "consumer_grants": [
                g for g in registry.consumer_grants if g.consumer_id is not ConsumerId.ASSISTANT
            ]
        }
    )
    assert _refusal(dataclasses.replace(selection, registry=no_grant)) == "assistant_not_granted"
    emitting = ConsumerGrant(
        consumer_id=ConsumerId.ASSISTANT,
        purposes=[BundlePurpose.SETUP_DETAIL],
        permissions=sorted([ConsumerPermission.EMIT_INFERENCE, ConsumerPermission.READ_FACTS]),
        machine_decision_mode_allowed=False,
    )
    widened = registry.model_copy(
        update={
            "consumer_grants": [
                emitting if g.consumer_id is ConsumerId.ASSISTANT else g
                for g in registry.consumer_grants
            ]
        }
    )
    assert (
        _refusal(dataclasses.replace(selection, registry=widened))
        == "assistant_grant_not_read_only"
    )
    production = registry.model_copy(update={"registry_label": "production-registry"})
    assert _refusal(dataclasses.replace(selection, registry=production)) == (
        "assistant_synthetic_only"
    )


# --- Isolation ----------------------------------------------------------------------------------

_FORBIDDEN_MODULES = (
    "duckdb",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "os",
    "pathlib",
    "subprocess",
    "random",
    "time",
    "dotenv",
    "openai",
    "anthropic",
    "streamlit",
    "market_intelligence.data_connectors",
    "market_intelligence.storage",
    "market_intelligence.evidence_store",
    "market_intelligence.model_clients",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.config",
    "market_intelligence.dashboard",
)
_FORBIDDEN_NAMES = ("order", "broker", "robinhood", "execut", "submit", "rank", "probab")


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_assistant_imports_no_model_network_database_or_order_code(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            for name in names:
                assert not any(name == m or name.startswith(m + ".") for m in _FORBIDDEN_MODULES)
        if isinstance(node, ast.FunctionDef | ast.ClassDef | ast.Name):
            ident = (node.id if isinstance(node, ast.Name) else node.name).lower()
            assert not any(w in ident for w in _FORBIDDEN_NAMES), (module, ident)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"now", "utcnow", "today", "getenv"}, node.func.attr


def test_answering_opens_no_database_file_or_connection(monkeypatch):
    selection = _selection("unresolved_critical_conflict")

    def refuse(*args, **kwargs):
        raise AssertionError("the assistant must not open a database, file or connection")

    monkeypatch.setattr(duckdb, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(builtins, "open", refuse)
    answers = A.answer_all(
        selection, card_id=selection.card.card_id, bundle_id=selection.bundle.bundle_id
    )
    assert set(answers) == set(A.QuestionId)
