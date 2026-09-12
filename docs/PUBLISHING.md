# Publication boundary

This project is currently **unlicensed**, by the owner's choice. No open-source
reuse license is granted by placing source on GitHub. Copied third-party material
retains its original terms and is not relicensed here.

## Include

- Python source, bounded scripts, tests, lockfiles and setup documentation.
- Frontend source and curated `frontend/public/` exports, including the October
  event dashboard. Saved browsing does not need a backend or archive mount.
- Adapter support limits and dated research reports, with local references treated
  as unavailable evidence unless the corresponding public provenance is supplied.

## Keep local

The root ignore rules exclude `.env`, raw/intermediate `outputs/`, `data` (including
its archive symlink), backups, environments, caches, build output and local agent
configuration. Copied `evidence/` and its original copy manifests are excluded
pending a separate provenance/distribution review. Nothing is deleted locally.

Do not use `git add -f` to include excluded archives. Before a first push, inspect
the exact staged file list and run a secret scan that reports paths without
printing credential values. A successful test run is not a secret audit.

## Acceptance

Use the checks in the root README. Also test an isolated checkout without local
archives: portable tests and frontend build must work, and archive-dependent
checks must be explicitly identified. A successful local replay with private
cached state does not establish a reproducible fresh installation.

Code publication and public service hosting are separate. Saved dashboards need
only their exports. Enabling the quote backend publicly additionally requires
access controls and request limits; it remains an offline research service.

Local handoffs and task records are excluded. The private-derived policy replay,
its dashboard, exports and tests are also excluded pending a separate provenance
review. These files remain locally available; the public dashboard index does not
advertise them. Current entry points are README.md, ADAPTERS.md and DASHBOARDS.md.

The initial GitHub credential did not include `workflow` scope. The tested workflow
is retained locally and excluded from this initial release; local checks are
verified, but GitHub Actions is not enabled.
