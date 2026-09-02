"""Placeholder entries for well-known ECM/DMS/cloud-storage platforms that
show up in the Connections grid for visibility, but have no real
StorageProvider behind them yet — deliberately NOT registered in
registry.py, so create_connection/oauth_start can never be called against
one of these (there's no adapter to call). Each is rendered with
coming_soon=True so the frontend can show a "Coming soon" badge and make
the card non-actionable instead of silently failing or looking broken.

Keep this list to real, recognizable products only — no invented names.

Currently empty: every platform that was on this list has a real (if, for
several of them, unverified-and-honestly-caveated) StorageProvider now.
"""

COMING_SOON_PROVIDERS: list[dict] = []
