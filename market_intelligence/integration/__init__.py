"""Offline Evidence-to-Dashboard vertical slice (PROJECT_STATE item 66).

Synthetic source objects -> producer adapters -> Evidence Envelopes ->
temporary Evidence Store -> point-in-time bundles -> synthetic setup
evaluation -> setup cards -> dashboard views built only from records read
back from storage. Deterministic, synthetic and temporary: no real database,
provider, model, network, production registry or setup definition.
"""
