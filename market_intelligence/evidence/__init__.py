"""Shared Evidence Envelope core (``evidence-envelope-1``).

Offline implementation of the reviewed design in
``docs/EVIDENCE_ENVELOPE_DESIGN.md``, ``docs/EVIDENCE_REGISTRY.md`` and
``docs/EVIDENCE_CONSUMER_RULES.md`` (reviewed merge ``ac541c9``), limited to
the §Q.5 authorization: contracts, canonical serialization, content-addressed
IDs, validators, freshness evaluation, conflict and supersession validation,
point-in-time bundle construction, the Contract Selector boundary, and the
SPY holdout guard.

This package is pure and offline. It never imports a data connector, model
client, storage layer, orchestration code or network library, opens no
database, and makes no request. It contains no producer adapter, no store,
no populated registry, no consumer UI, no ranker and no notification code.
"""
