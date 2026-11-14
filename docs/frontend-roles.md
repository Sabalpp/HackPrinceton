# Three authenticated FarCare views

The production UI signs in through `/api/auth/*` and uses an HttpOnly session cookie. The typed client includes credentials and sends the session's CSRF value on mutations. It never reads or stores the cookie, and it has no public shared family-token fallback. Production uses the same-origin `/backend` proxy; local development can explicitly set `NEXT_PUBLIC_API_URL=http://localhost:8000` with the API's allowed-origin configuration.

| Route | Role | What the screen does |
| --- | --- | --- |
| `/login` | Public | Sign in; account creation only when the API enables registration |
| `/join#token=…` | Invited person | Look up a private invitation, create an account with the server-assigned role, and choose parent sharing permissions |
| `/setup` | Family or parent | Create a parent profile/invitation and invite a helper; the parent approves helpers |
| `/parent` | Parent | Large check-in/help/BP actions, explicit request approval, sharing controls, helper approval, and provisional personal references |
| `/`, `/family`, `/health`, `/requests`, `/circle` | Family | Existing live dashboard, personal learning progress, source labels, and the closed-loop request history |
| `/respond` | Helper | Assigned tasks only, with accept/decline/complete controls; no family health requests or health UI |

The API is the authorization boundary. Client role gates route people to the correct home and prevent unrelated data providers from mounting. Family views clear protected data on authorization failures. Parent and helper views poll every ten seconds while visible; the family dashboard uses authenticated SSE with a thirty-second refresh fallback. A connected SSE stream describes app connectivity, not sensor freshness or medical status.

Invitations remain in memory and use a URL fragment, so the invitation token is not sent in the page URL to the web server. Copying an invitation is an explicit user action. No account, session, or invitation credential is stored in localStorage. Registration is not described as email verification; delivery and account recovery require their own configured services.

Development role previews appear only when the API advertises `demo_available`. The API's `demo_session` flag labels the current account as a demo; it does not relabel real health records as simulated. Real, mixed, and simulated health sources remain visibly distinct. Old concepts are accessible separately under `/concept` and have a concept banner; they are absent from real account navigation.

## Closed-loop interaction

1. Parent opens **Ask for help**, enters the task, and chooses **Review my request**.
2. Parent separately chooses **Yes, ask for help**. Until that approval, the UI does not claim a helper was contacted.
3. The assigned helper opens `/respond`, chooses **Yes, I can help**, and later **Mark complete**.
4. Parent and family views refresh from server state. Phone delivery is not claimed when only in-app or console messaging is configured.

Check-ins, task creation/decisions, responder replies, BP entry, and onboarding creation retain an `action_id` across failed retries. Changing the intended action/details gets a new ID. Buttons block concurrent submissions. Non-idempotent mutations must not be silently retried by the HTTP client.

## Personal health presentation

The UI displays the API's qualified-day counts, observed median, exclusions explanation, and learning status. It does not compute clinical thresholds, invent missing readings, treat an individual reference as a safe limit, or infer BP from Fitbit. The seven-day learning rule is described as app coverage rather than medical calibration. User references remain provisional and never change safety guidance.

BP readings come from manual cuff entry, with measurement time, symptom review, scope confirmation, and device-validation self-report. Returned guidance is displayed with its original reading date as **Guidance at time recorded**. Historical guidance is not presented as a new live alert. Empty, paused, private, and offline states stay explicit.

## Verification

Run the frontend lint and type checks, then the synthetic browser workflow in CI. Browser evidence must cover login/role routing, parent approval, helper acceptance/completion, family refresh, and helper privacy. No real messaging or private health export is needed for these checks. Local sandbox restrictions may prevent starting the production web server; lint/type checks alone are not a visual/browser pass.
