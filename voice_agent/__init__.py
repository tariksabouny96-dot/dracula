"""HOOD voice worker: the process that gives Hood ears and a mouth during a call.

It joins the call's LiveKit room, listens, and hands every utterance to the one Hood through the
authenticated ``/api/voicecall/agent/*`` endpoints (per-call credential). It holds no Hood logic, no
approvals and no Hood secrets. Only this package imports LiveKit Agents / Gemini Live.
"""
