# HOOD controlled-simulation acceptance — October 2026

## Scope

The `services.simulation` integration harness is intentionally inert. It tests the
shape of voice, browser, provider, and desktop actions and the governance contract
without interacting with a real device, user file, payment instrument, or network.
Its receipt is always `CONTROLLED_SIMULATION`, every action is marked `SIMULATED`,
and `production_acceptance` is always `false`.

Run:

```bash
python -m pytest -q tests/simulation
python scripts/controlled_simulation.py
python scripts/preproduction_gate.py
```

The gate must remain **NO-GO** until target-machine and external integration
acceptance evidence exists. Simulations are not substitutes for hardware, live
provider, browser, deployment, or independent security acceptance.

## Voice safety change

Earlier voice adapters synthesized a fixed transcript and silent PCM while
reporting successful audio operation. The Gemini Live adapter now raises
`NotImplementedError` rather than misrepresenting unavailable STT/TTS services.
The voice capability inventory is labeled as not implemented; waking/VAD alone
is not described as full recognition or synthesis. Tests using fabricated audio
must use the explicit simulation harness, not the production voice router.

## Further work before GO

- Implement actual opt-in audio capture, STT, and TTS transports and test on Windows.
- Run authenticated browser tests with a permitted browser environment.
- Test native desktop automation against a disposable Windows virtual machine.
- Verify real Gemini/OpenAI provider consent, quotas, errors and billing.
- Replace inert simulations with independently verified real-world test evidence.
- Resolve the remaining full-suite and capability acceptance blockers.
