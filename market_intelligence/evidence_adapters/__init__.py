"""Offline Evidence producer adapters (PROJECT_STATE item 65).

Pure translation from already-validated source objects into the reviewed
Evidence Envelope contracts:

- ``market_data``: Alpaca SPY bars and snapshots (confirmed facts);
- ``fred``: FRED observations and series metadata (confirmed facts; missing
  values as explicit missing evidence);
- ``news``: Alpaca news publication metadata (headline and summary are
  display-only, provider-reported and unverified);
- ``clock``: injected clock-health readings;
- ``market_calculations``: already-produced regime-engine results as
  deterministic calculations with complete parent lineage.

No database, file, environment, network, clock, randomness or model access.
No registry file is created and no producer is activated.
"""
