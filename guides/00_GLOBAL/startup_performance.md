---
guide_id: global.startup_performance
title: Neo Studio Startup Performance
surface: global
scope: built_in
applies_to:
  - startup
  - browser_boot
  - windows
  - performance
tags:
  - startup
  - performance
  - windows
  - troubleshooting
priority: 90
version: 1
updated: 2026-08-14
---

# Neo Studio Startup Performance

Neo startup is split into two browser-loading stages.

1. **Essential Image shell** loads first: surfaces, backend profiles, extensions, Image model-family/parameter data, Image node-manager state, Image base state, preview actions, and the Image prompt library.
2. **Deferred workspaces** then load in a bounded background queue: Admin diagnostics, Video route state, Voice histories/capabilities, Project data, Roleplay state, Assistant bootstrap, and non-Image UI presets.

The deferred queue intentionally limits concurrent requests instead of starting every endpoint at once. This keeps first paint faster and avoids large connection bursts on Windows.

On Windows, a browser refresh or abandoned request can produce `WinError 10054` while the Proactor transport is closing a client socket. Neo filters only the known `_ProactorBasePipeTransport._call_connection_lost` cleanup callback. Other connection errors remain visible/logged.

If startup is still slow, inspect `neo_data/logs/neo_server.log` and browser Network timing to identify a specific endpoint rather than increasing request concurrency globally.
