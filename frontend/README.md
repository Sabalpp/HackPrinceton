# FarCare frontend

This folder contains the Next.js screens for parents, family members and helpers.
Owner: Saksham. Instructions: [UI team guide](../docs/agents/SAKSHAM.md).

| Start here | What it does |
| --- | --- |
| `app/(app)/` | Family dashboard, health, activity and care-circle screens |
| `app/parent/`, `app/respond/` | Parent check-ins and helper tasks |
| `app/login/`, `app/setup/`, `app/join/` | Sign-in, account setup and invitations |
| `components/` | Reusable UI pieces |
| `lib/api.ts` | Calls the backend with the authenticated browser session |
| `lib/dashboard.tsx` | Loads dashboard state and timeline updates |
| `lib/api-types.ts` | Generated types from the shared API contracts |
| `app/globals.css` | Shared styling |
| `public/` | Static images, icons and other public assets |

`app/(web)/` contains older concept screens. The connected care flow uses the
routes listed above. Health calculations, secrets and Photon/Gemini calls live in the
[backend](../backend/README.md).

From this folder:

```bash
test -f .env.local || cp .env.example .env.local
npm ci
NEXT_PUBLIC_USE_MOCKS=false NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
# Open http://localhost:3000; start the backend in another terminal.
npm run gen:api              # regenerate lib/api-types.ts after a contract change
npm run lint && npm run typecheck && npm run build
```

Use `NEXT_PUBLIC_USE_MOCKS=true` only for explicitly simulated UI development.
Never put database or messaging secrets in a `NEXT_PUBLIC_` variable. To deploy the
dashboard and backend together on Vercel, select the repository root and follow
[the deployment guide](../docs/vercel-deployment.md).
