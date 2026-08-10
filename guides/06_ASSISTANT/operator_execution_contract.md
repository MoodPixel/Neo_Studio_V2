# Operator Execution Contract — Phase 13

Status: **Implemented**  
Date: **2026-08-09**

Neo Operator is no longer a second general-purpose intent brain.

The canonical responsibility chain is:

```text
User request
  -> Assistant / Control Center
     understands the request
     decides whether a runtime action is needed
     creates a structured action request
  -> Operator
     checks Tool Registry permissions
     enforces confirmation
     executes only registered actions
     returns execution receipts
     records ledger proof
  -> Assistant
     may claim an action happened only when receipt proof supports that claim
```

## Canonical structured handoff

The Assistant/Control Center action request uses:

`neo.control_center.action_request.v1`

It carries explicit actions rather than prose for Operator to interpret.

Typical action fields:

- `action_id`
- `action_type`
- `label`
- `effect_class` (`read`, `write`, `external`, `advisory`)
- `risk_level`
- `requires_confirmation`
- `payload`
- canonical Scope/Surface/Delivery Project identity
- Control Center trace reference where available

## Operator responsibility

Operator may:

- apply the central Tool Registry permission profile;
- block unregistered actions;
- require confirmation;
- execute registered action dispatchers;
- normalize success/failure/block results;
- issue `neo.operator.execution_receipt.v1` receipts;
- aggregate `neo.operator.execution_proof.v1`;
- record ledger events.

Operator must not:

- classify arbitrary human requests into general domains;
- choose memory sources because of its own human-intent classifier;
- answer normal user questions;
- invent a tool/action that Assistant/Control Center did not request;
- treat a successful API envelope as proof that the requested mutation happened.

## Confirmation

Read-only actions may run without confirmation when the active Tool Registry profile allows them.

Write/external actions are confirmation-gated when the registry requires confirmation.

An unconfirmed run can still be operationally successful while the requested effect remains blocked. This is why the outer `ok` field is **not** action-success proof.

## Execution receipts

Each attempted action produces a receipt with:

- receipt ID;
- action ID/type;
- tool ID;
- status;
- success boolean;
- confirmation state;
- effect class;
- ledger ID;
- bounded result payload;
- `claimable_success`.

`claimable_success` is reserved for successful write/external effects. A read-only lookup does not authorize Neo to say it sent, changed, deleted, scheduled, uploaded, or published something.

## Capability truth

Assistant output validation now accepts external/write success only from receipt-backed proof.

These are **not** sufficient:

```text
operator_result.ok = true
operator_result.status = completed
```

because a run may contain blocked confirmation-required actions.

Valid proof requires a successful execution receipt/proof carrying `claimable_success=true`.

## Compatibility text routes

Historical routes remain:

- `POST /api/operator/plan`
- `POST /api/operator/run`

They are compatibility adapters only. Free text is translated by the Assistant/Control Center action planner before Operator sees the request.

Canonical Phase 13 routes are:

- `POST /api/operator/actions/plan`
- `POST /api/operator/actions/execute`

## Voice bridge

Voice remains an input layer. A transcript is first routed through Assistant Action Review / Control Center planning. Operator then receives the same structured action request used by text UI.

## Fail closed

When a user asks for an effect that has no registered Neo tool—for example an external send action with no corresponding runtime integration—the Control Center can represent it as unsupported, and Operator blocks it.

Confirmation cannot turn an unregistered tool into a real capability.
