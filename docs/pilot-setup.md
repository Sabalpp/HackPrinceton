# Invited pilot: three roles, one care loop

This release implements account and care workflows. It does not assert that a public
deployment, live Photon delivery, a verified cuff, or clinical validation has happened.

## Start with one consenting family

1. Prepare PostgreSQL, an always-running host, HTTPS, and real provider credentials
   using [the deployment runbook](production-runbook.md). The production configuration
   refuses demo seeding, SQLite, an API-owned background worker, or HTTP CORS origins.
2. Inside the API environment, create the first family account:

   ```bash
   python -m app.manage_accounts create-owner --email owner@example.test --name "Family member"
   ```

   Substitute the independently verified person's account identifier. The command
   prompts for a password without echoing it. Never send passwords in arguments,
   commits, tickets, or chat logs. Email is a login identifier; this implementation
   does not claim email verification, public signup, or automated recovery.
3. Sign in at `/login`. `/setup` creates the parent's profile, timezone and country,
   and a private invitation link. Deliver that link yourself to the intended parent.
   Invitations expire in two days and can be used once. Retried setup submissions
   reuse their action identifier and do not create duplicate profiles.
4. The parent accepts their invitation and chooses health/check-in sharing. Both are
   initially off. They can later change them in **My sharing & Care Circle**.
5. The family invites a nearby helper. The helper creates their own account; the
   parent approves membership and separately chooses whether they may help with
   safety checks. Registered helpers can receive tasks in `/respond` without a phone
   messaging provider. This requires opening the app; it is not a push notification.
6. A routine request follows: parent asks → parent confirms → assigned helper accepts
   → helper completes → parent and family see the result. An urgent-help action uses
   the separately approved emergency-contact policy. FarCare never calls emergency services.

The invitation fragment is not sent as part of the URL request. Lookup/redemption use
POST bodies. Invitation hashes are stored in the invitation table; setup idempotency
receipts temporarily retain the invitation response for a reliable retry. Protect the
database and backups accordingly. Do not log request bodies or copy invitation links
into public issues. An accepted/expired invitation cannot be revived by replaying setup.

For operator-assisted recovery, independently verify the account owner, then run:

```bash
python -m app.manage_accounts reset-password --email owner@example.test
```

This revokes every existing session. A chosen production identity provider, MFA,
verification/recovery UX, abuse controls and independent security review remain launch
decisions, not capabilities claimed by this private-pilot login.

For an operator-managed sign-in name, set the server-only `LOGIN_ALIASES` JSON
object, for example `{"caregiver":"owner@example.test"}`. The target account must
already exist. The alias uses its normal password, rate limit, session and role
permissions. Registration and invitations still require email addresses and the
usual password policy. An alias grants no administrative role or access to another
family's records. Keep temporary shared demonstration accounts separate from real
wearers and do not grant them health-data access.

## Connect the correct real data

The existing Tiger adapter is machine-authenticated and takes an explicit destination
parent ID. See [Tiger data mapping](tiger-health-pipeline.md). Bind a sensor/account to
the newly created parent's ID only after independently checking ownership and consent.
Do not copy one sample parent's stream to all newly registered families. Ingestion keys
and Google credentials stay server-side. The current collector still requires the
wearer's BLE gateway or cloud sync; deploying the web app does not move that gateway.

Blood-pressure entry is manual from a cuff, with a recorded timestamp and measurement
context. It is not computed from Fitbit heart rate. The app describes coverage and
missingness instead of inventing unavailable metrics. No diabetes diagnosis, recovery
score, or fall detection from low step counts is claimed.

## Check operations before inviting anyone

Run the API and `python -m app.worker` as separately supervised services. `/health`
checks API connectivity; `/ready` additionally requires a recent successful worker cycle
in production. `python -m app.worker --check` checks worker readiness. Role screens show
a stale/offline agent when the heartbeat is missing. Monitor queue age and failed jobs;
a heartbeat alone cannot prove that a recipient received a message.

Back up and test restoration of the database, set an operator retention/deletion policy,
validate consent revocation and role access, and exercise provider outage/restart paths
with consenting test participants before relying on outreach. The automated browser
checks use synthetic records and console messaging; they do not verify phone delivery.

Provider opt-in/routing is configured separately from web account creation. A web login
email is never silently treated as an iMessage address. Complete the explicit operator
Photon setup in the runbook, including a real signed inbound message and real reply,
before demonstrating the sponsor's live-channel requirement. No provider line was bought
or message sent by this implementation.
