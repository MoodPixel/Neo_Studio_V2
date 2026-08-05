# IR-6.0 — Image Tab Recovery Hotfix

## Purpose

Recover the live Image workspace after the IR preset integration exposed a frontend inheritance-normalization mismatch.

## Root cause

The backend Image base contract can serialize an omitted sampling-preset `inherit` block as `{}` (or as an object containing only null values). The headless browser resolver treated every object as a real inheritance declaration. For a direct `Default · Balanced` entry this selected the same entry as its own parent and raised an inheritance-cycle exception while the unified Preset dropdown was rendering.

Because the dropdown checks Balanced availability during every Image render, the exception aborted the whole Image tab even when `Manual / No Preset` was selected.

## Locked behavior

- Empty/null-only inheritance means **direct preset**.
- Genuine inheritance remains materialized normally.
- A future malformed preset resolution cannot abort the Image workspace; the live renderer fails closed and treats the built-in as unavailable, allowing `Manual / No Preset` to remain usable.
- Other surfaces are unchanged.

## Phase relationship

This is an emergency recovery boundary before IR-6 Mount + Enable Recovery continues. It changes no Scene Director route or enable policy.
