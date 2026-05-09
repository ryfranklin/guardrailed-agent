# web

Vite + React 18 + TypeScript SPA for the Phase 3.a public web demo at
`demo.ms3dm.tech`. Sign-in is Cognito Hosted UI via `@aws-amplify/auth`;
the chat UI calls `POST /ask` against the API Gateway HTTP API.

Source-of-truth specs:
[phase-3a-brief §11](../docs/phase-3a-brief.md#11-web-spa-spec-web),
[ADR-012](../../consulting/guardrailed-agent/decisions/012-web-demo.md).

## Stack

- **Framework:** React 18 (SPA, no Server Components)
- **Bundler:** Vite 5
- **Language:** TypeScript 5 strict
- **Styling:** Tailwind CSS 3
- **Auth:** `@aws-amplify/auth` 6 (the library, not the Amplify framework)
- **HTTP:** native `fetch` with a thin wrapper at `src/api/client.ts`
- **Routing:** `react-router-dom` v6, two routes (`/`, `/auth/callback`)
- **Tests:** Vitest + `@testing-library/react`

## Local dev

Prereqs: Node 20.x LTS and pnpm 9 (Corepack ships pnpm — `corepack enable pnpm`).

```bash
cd web
pnpm install --frozen-lockfile
cp .env.example .env.local            # then populate from `terraform output`
pnpm dev                              # http://localhost:5173
```

`pnpm dev` opens the SPA shell. Sign-in only works once `.env.local` is
populated with values from a deployed Cognito user pool — until then the
auth flow logs a console warning and stays at the redirect screen.

### Build / test commands

| Command | Purpose |
|---|---|
| `pnpm typecheck` | `tsc --noEmit`. |
| `pnpm lint` | ESLint with `--max-warnings=0`. |
| `pnpm test` | Vitest run, jsdom env. |
| `pnpm build` | Type-check + Vite production build to `dist/`. |
| `pnpm preview` | Serve `dist/` locally for sanity-checking. |

`pnpm build` writes the bundle to `web/dist/`; that directory is what
§5.6 (the `web-demo` Terraform module) syncs to S3 and what `web.yml`
deploys via `aws s3 sync` + CloudFront invalidation.

### Build output

```
web/dist/
  index.html
  favicon.svg
  assets/
    index-<hash>.js
    index-<hash>.css
    ...
```

CloudFront serves `dist/` with a SPA fallback (any non-asset 403/404
rewrites to `/index.html`); see §5.6.

## Build-time env vars

These are read at `pnpm build` time and inlined into the bundle. The CI
workflow populates them from Terraform outputs via repository
`vars` (see `.github/workflows/web.yml` in §5.7).

| Var | Source (terraform output) | Notes |
|---|---|---|
| `VITE_API_ENDPOINT` | `module.gateway.api_endpoint` | Base URL; SPA POSTs to `<endpoint>/ask`. |
| `VITE_USER_POOL_ID` | `module.auth.user_pool_id` | Cognito user pool ID. |
| `VITE_USER_POOL_CLIENT_ID` | `module.auth.user_pool_client_id` | App client (no secret). |
| `VITE_COGNITO_DOMAIN` | `module.auth.hosted_ui_domain` | FQDN of the Hosted UI. |
| `VITE_REDIRECT_SIGN_IN` | `https://demo.ms3dm.tech/auth/callback` | Must match a Cognito callback URL. |
| `VITE_REDIRECT_SIGN_OUT` | `https://demo.ms3dm.tech/` | Must match a Cognito logout URL. |
| `VITE_AWS_REGION` | `us-east-1` | Reserved for future use; not directly consumed by Amplify Auth v6. |

`.env.example` contains placeholders for all seven; `.env.local` is
gitignored.

## Source layout

```
src/
  main.tsx                # Amplify.configure + StrictMode + Router mount
  App.tsx                 # Persona modal vs ChatView state machine
  routes.tsx              # / (auth-gated) + /auth/callback
  auth/
    amplifyConfig.ts      # Amplify.configure() driven by import.meta.env
    AuthGate.tsx          # checks session; redirects to Hosted UI if signed out
    AuthCallback.tsx      # Cognito redirect-handling page
  components/
    PersonaModal.tsx      # full-screen blocker until a persona is picked
    PersonaIndicator.tsx  # header chip with [change] button
    ChatView.tsx          # MessageList + ComposerInput + ErrorBanner
    MessageList.tsx       # message bubbles; auto-scrolls
    ComposerInput.tsx     # textarea + Enter-to-send
    ErrorBanner.tsx       # dismissable error
  api/
    client.ts             # postAsk() with fetchAuthSession + fetch
    types.ts              # PersonaRole + AskRequest/AskResponse + ApiError
  state/
    persona.ts            # usePersona hook (React state, no global store)
    session.ts            # useSession returns sessionStorage-backed id
  styles/
    globals.css           # Tailwind directives
  test/
    setup.ts              # jest-dom matchers for Vitest
```

## Persona picker UX

- Modal blocks the chat at session start until a persona is picked.
- Header `PersonaIndicator` shows the active persona; `[change]` reopens
  the modal.
- Picking a new persona calls `usePersona().clearPersona()`, then on
  confirm we set the new persona and let `<ChatView>`'s `key` re-mount
  it — fresh message state, no leakage between personas.
- `technician_lead` requires a non-empty `service_region` (default
  `tempe-mesa` per ADR-008's HVAC dataset). Other personas drop the
  field server-side; the SPA also refuses to submit without one.

## Tests

Vitest (jsdom) covers:

- `PersonaModal` — confirmation gate, technician_lead service-region
  requirement, defaultRole respect, cancellable behavior.
- `usePersona`, `useSession` — state transitions and sessionStorage
  behavior.
- `api/client.postAsk` — bearer-token wiring, JSON error mapping,
  non-JSON error fallback, missing endpoint and missing token
  short-circuits.

Run with `pnpm test`. AWS-touching paths are mocked at the
`@aws-amplify/auth` boundary; no live calls.
