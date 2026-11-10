## Project rules
- All backend access goes through `src/services/` (`api` from `@/services`); only `src/services/http.ts` calls fetch. Why: the FastAPI backend will be built against this contract.
- `VITE_USE_MOCK` (default true) selects `mockService`, which emulates server rules (ordering, permissions, audit). Why: app runs with no backend.
- The UI never sorts or scores cases; it renders service order. Alert/note text is rendered as plain text with whitespace-pre-wrap only.
- Auth session is client-only (sessionStorage); pages gate through `AppShell`. Why: no SSR auth.
