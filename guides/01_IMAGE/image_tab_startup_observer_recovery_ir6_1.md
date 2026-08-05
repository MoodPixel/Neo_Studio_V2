# IR-6.1 — Image Tab Startup Observer Recovery

## What this fixes

Use this hotfix when Neo starts but the browser stays on **Loading…** after the recent Image preset and Scene Director integration changes.

## Root cause

The live negative-prompt eligibility helper follows Image workspace remounts with a `MutationObserver`. It was rewriting its own status-hint text on every refresh, even when the message had not changed. Replacing that text created another observed DOM mutation, so the browser repeated the same refresh indefinitely and never completed the visible Image startup.

## Locked behavior

- Eligibility hints update only when their value actually changes.
- Bursts of Image DOM mutations are collapsed into one queued refresh.
- A refresh cannot re-enter itself.
- Eligibility events are not emitted repeatedly for an unchanged result.
- User negative-prompt text remains retained exactly as before.
- Sampling presets and Scene Director routing are not changed by this recovery patch.

## After applying the changed files

1. Restart Neo Studio.
2. Hard-refresh the browser once so the corrected JavaScript replaces any cached copy.
3. Open the Image tab and confirm the title changes from **Loading…** to **Image**.

No model reinstall, preset reset, Scene Director data reset, or backend reconfiguration is required.
