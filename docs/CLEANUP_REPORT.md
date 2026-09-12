# Publication cleanup verification

Verified 2026-09-12. This records local checks, not a GitHub CI or deployment run.

| Check | Result |
| --- | --- |
| Full local Python suite with benchmark extra, excluding RPC | 404 passed, 1 browser skip, 1 RPC deselected |
| Isolated publication copy, without data/outputs/evidence; portable selection | 314 passed, 1 browser skip, 86 deselected (85 archive cases plus RPC); private-derived replay tests excluded from publication |
| Ruff: src, tests, scripts | Passed |
| Installed wheel with explicit prepared root | Cached historical quote succeeded, zero network requests |
| New web entrypoint and flag checks | Passed; host/port/result-directory flags are parsed |
| Frontend tests | 49 passed |
| Frontend typecheck, lint, production build | Passed; build required unrestricted local process execution |
| Publication candidate scan | Approximately 55 MB; no archive symlink, credential-shaped provider URLs, assigned API credentials, private-key headers or personal machine paths detected |
| Shell helper syntax and documentation links | Passed |

The content scan is pattern-based, not a guarantee of absence of all secrets.
Raw archives, environments, copied reference evidence and benchmark recordings
are ignored and remain intact locally. No collectors were restarted.

Saved dashboards work from bundled exports. New quotes require prepared qualified
state via SWAPARCH_ROOT. The project remains unlicensed by owner choice. These checks ran on the selected
public file set before its first commit. Public source publication does not deploy
a service. Private-derived replay and internal handoffs remain local and excluded.
