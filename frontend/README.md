# SOCBoard frontend

The React + TypeScript single-page app for SOCBoard, an alert triage case manager for a security operations centre. It builds to static files.

Install and build from the repository root with `make frontend-install` and `make frontend-build`.

## Environment variables

Vite reads these at build time (and from the shell when the dev server starts). All four are optional.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_USE_MOCK` | on (any value except the string `false`) | `false` uses the HTTP client against the real backend; anything else, or unset, uses the in-memory mock. |
| `VITE_API_BASE_URL` | `/api` | Where the HTTP client sends requests. An empty value gives `/api`. |
| `VITE_POLL_INTERVAL_MS` | `3000` | How often the queue refreshes, in milliseconds. A value that is not a positive number gives `3000`. |
| `VITE_FEATURE_REASSIGN` | follows the mock: on in mock mode, off otherwise | `true` shows the Lead's Reassign control, `false` hides it. Any other value follows the mock. It hides the control only; the service (the mock now, the backend later) still refuses a reassign by anyone who is not a Lead. |

In mock mode a new demo alert is added to the queue every 20 seconds. The timer is off when `VITE_USE_MOCK` is `false`.

## Service layer

The screens call `api` from `src/services`, which is the mock or the HTTP client. Both implement the `SocService` interface in `src/services/types.ts`:

| Function | Returns |
| --- | --- |
| `login(username, password)` | `LoginResponse` (token and `User`) |
| `getQueue()` | `QueueResponse` (`CaseSummary[]` in service order, and `servedAt`) |
| `getClosedCases()` | `CaseSummary[]` |
| `getCase(id)` | `CaseDetail` (a `CaseSummary` plus description, severity rule and `Note[]`) |
| `claimCase(id)` | `CaseDetail` |
| `transitionCase(id, to, closingReason?)` | `CaseDetail`; `to` is a `Status`, `closingReason` a `ClosingReason` |
| `reassignCase(id, userId)` | `CaseDetail` |
| `addNote(id, text)` | `Note` |
| `getAudit(id)` | `AuditEntry[]` (the `actor` is a `User` or `"system"`) |
| `listUsers()` | `User[]` |

A refused call throws `ApiError` (`status`, `code` and a `message` the screens show). The other types are `Role`, `Severity`, `AssetCriticality`, `AuditAction`.

## Tests

Run `make frontend-test` from the repository root. In a fresh environment run `make install` once first; `make frontend-test`, `make test` and `make verify` fail until the frontend packages are installed. Tests are `*.test.ts` and `*.test.tsx` files under `src/` (Vitest with jsdom and Testing Library).

## What the production-bundle check does not prove

`make frontend-build` builds with the mock off and then searches the built files for four literal strings: `analyst-a-pass`, `analyst-b-pass`, `lead-pass` and `<script>alert(`. It catches an accidental inclusion of the mock. It does not catch a deliberate one: a string that is base64-encoded, split or concatenated passes. It does not search for the other two hostile strings (the `<img src=x onerror=alert(1)>` payload and the "Ignore previous instructions" line), and it does not search for the demo alert's text.

It is a guard, not a boundary. What keeps the mock out of a production bundle is the literal comparison `import.meta.env.VITE_USE_MOCK !== "false"` in `src/services/index.ts` (the same literal is written out in `src/routes/__root.tsx` and `src/routes/login.tsx`), which lets the bundler drop the mock when the variable is `false`.
