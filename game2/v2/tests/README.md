# Tests

Cross-domain verification for Game2 V2. Tests may inspect every domain and run
Console and Player as separate processes, but production domains must obey the
dependency rules.

Entrypoints: `harness.py` for realtime smoke and `test_v2.py` for unit and
architectural tests. Local documentation: `doc/`.
