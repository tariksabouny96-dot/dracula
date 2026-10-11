"""HOOD voice calling: two-way real-time calls between the owner's iPhone and the one Hood.

See docs/VOICE_CALLING_ARCHITECTURE.md. Vendor specifics (LiveKit, Gemini Live, APNs) sit behind small
interfaces: ``RealtimeTransport`` (transport.py), ``ProviderProfile`` (profiles.py) and
``ApnsTransport`` (push.py). Nothing here imports a vendor SDK.
"""
