# FarCare web (Next.js)

Owner: Saksham. Instructions: [docs/agents/SAKSHAM.md](../../docs/agents/SAKSHAM.md).

```bash
cp .env.example .env.local   # NEXT_PUBLIC_USE_MOCKS=true to work without the backend
npm install
npm run dev                  # http://localhost:3000
npm run gen:api              # regenerate lib/api-types.ts after a contract change
npm run lint && npm run typecheck && npm run build
```
