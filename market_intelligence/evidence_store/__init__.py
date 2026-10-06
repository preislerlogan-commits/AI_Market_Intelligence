"""Offline evidence, registry and setup-card storage core.

Implements docs/EVIDENCE_CARD_STORAGE_DESIGN.md and
docs/REGISTRY_LOADING_DESIGN.md for temporary databases and temporary
checkpoint directories only. It refuses the real project database, contains no
producer adapter, dashboard, assistant, notification, ranking, brokerage or
execution code, registers no real registry and adds no setup definition.
"""
