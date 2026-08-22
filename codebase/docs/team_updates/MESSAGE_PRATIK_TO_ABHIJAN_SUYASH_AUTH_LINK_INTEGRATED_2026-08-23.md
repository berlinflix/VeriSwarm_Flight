# Pratik authorization-link integration

- owner: Pratik
- branch: `pratik/sih26177-disaster-cosys`
- commit: `0b2e7ee`
- consumed Abhijan implementation: `c74fb42`
- consumed latest Windows launcher fix: `9d1f03d`

## What works

Pratik now has the restricted Windows receiver at `192.168.50.11:8772`. It accepts only
Abhijan's wired `192.168.50.14` source, validates a complete canonical five-lease snapshot,
rejects stale/replayed/out-of-order input and atomically replaces the exact file consumed
by the live movement-v2 runner. No dashboard files were taken into Pratik's branch.

Cross-component tests prove Abhijan's generated five-event snapshot is consumed as current
`ALLOW` leases by Pratik's runner. Result: `26 passed`. Abhijan's imported functional suite
also produced `30 passed`; its only Windows failure is the non-portable POSIX permission-bit
assertion.

## Run

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_authorization_receiver.ps1
```

Then follow the frozen startup order in Abhijan's handoff and retain nominal ALLOW,
single-vehicle HOLD and terminal QUARANTINE runs. Limitation: live qualification is not yet
claimed; it requires both operators and the physically isolated Ethernet link.
