<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Swap Archeology frontend boundaries

Read ../docs/tasks/phase9-frontend.md. Own this frontend directory only. Do not
read .env files or any credentials, raw RPC caches, or external project files.
Do not start, stop, or assume the state of background collectors. No Git initialization,
commits, pushes, deployment, or remote knowledge writes. No mock financial data.
Public catalog is exported from real saved reports. Keep raw integers as strings
or BigInt, no browser RPC, and no browser exposure of SWAPARCH_API_URL.
Keep styling in design tokens and reusable UI primitives; explain the theme
extension points in DESIGN_SYSTEM.md. This is a historical research interface,
not a wallet app: no connect-wallet, execution, TVL invention or live-trade claims.
