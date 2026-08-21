# 07 · Creating the credentials

**Writer:** coordinator. **Audience:** whoever holds Azure and Teams admin rights.
This is the part the implementation agents **cannot** do — it needs a human with
directory and Teams permissions.

Everything here is needed by **Wave 2**. Wave 1 runs entirely against stubs, so
this can proceed in parallel with implementation.

---

## 0 · A personal Microsoft account will not work

Read this before spending time in the Azure portal.

**Building and sideloading a custom Teams app requires a work or school Microsoft
365 account. A personal Microsoft account cannot do it** — Microsoft's own
prerequisites say so plainly, and there is no workaround, flag or preview to turn
on. See [Prerequisites to create a Teams agent or app][prereq].

What a personal account *can* still do, which is why this is easy to get wrong:
you can sign up for an Azure subscription, create both **Azure Bot** resources,
generate their App IDs and secrets, and enable the Teams channel. All of that
succeeds. It is useless on its own, because you then have nowhere to install the
app — sideloading is where it stops.

Three ways to get a tenant that works:

| Route | What it costs | Catch |
|---|---|---|
| **Your organisation's existing Microsoft 365 tenant** | nothing | A Teams admin must enable custom app upload for your account — see [Manage custom app policies and settings][policies]. Most locked-down tenants have it off. |
| **Microsoft 365 Developer Program sandbox** | nothing, *if you qualify* | No longer open to everyone. It now requires a **Visual Studio Professional or Enterprise _standard_ subscription** (monthly subscriptions do not qualify), or membership of a Microsoft partner programme. See the [FAQ][devfaq] and [Join with Visual Studio][devvs]. |
| **A Microsoft 365 Business trial tenant you administer** | trial, then paid | You are the admin, so you can enable custom app upload yourself. Usually the fastest route when the first two are unavailable. |

If you are on a personal account and none of the three apply, **stop here** — the
gateway, the saga and the outbox work (Waves 1–2) can all be built and tested
against stubs without any of it, and only the live Teams gates need a tenant.

---

## 1 · Toolchain status on this machine

| Tool | Status | Needed for |
|---|---|---|
| Node | **v24.14.0** — satisfies the pinned major 24 | Agent A, all waves |
| npm | **11.1.0** | Agent A, all waves |
| `devtunnel` | **MISSING** | Wave 2 onward — blocker |
| `az` | **MISSING** | Optional; only for automated endpoint verification |

Install the tunnel CLI:

```powershell
winget install Microsoft.devtunnel
```

Azure CLI is optional. Without it, `validate_teams_bots.ps1` prints the exact
expected endpoints for manual confirmation instead of comparing them — the plan
requires that fallback, and gateway readiness never depends on it.

---

## 2 · Two Azure Bot registrations

**Do this twice** — once for Workflow, once for Support. They must be genuinely
separate identities or the two bots cannot appear as distinct senders.

1. Azure portal → **Create a resource** → **Azure Bot**.
2. Bot handle: `returns-workflow-agent-dev` / `returns-support-agent-dev`.
3. **Type of App: Single Tenant** (the plan freezes single-tenant).
4. Creation type: **Create new Microsoft App ID**.
5. After creation, from the bot resource record:
   - **Microsoft App ID** → `TEAMS_*_APP_ID`
   - **App Tenant ID** → `TEAMS_ALLOWED_TENANT_ID` (same for both)
6. **Create a client secret**: bot → *Configuration* → **Manage** beside the App ID
   → *Certificates & secrets* → **New client secret**.
   **Copy the Value immediately — it is shown once.** → `TEAMS_*_APP_PASSWORD`
7. **Messaging endpoint**: bot → *Configuration* → *Messaging endpoint*:

   ```
   https://<tunnel-host>/api/messages/workflow      (workflow bot)
   https://<tunnel-host>/api/messages/support       (support bot)
   ```

   Set this **after** the tunnel exists (§4). It changes whenever the tunnel URL
   changes, and **both** bots must be updated.
8. **Channels** → add **Microsoft Teams** → apply. Without this the bot cannot
   reach Teams at all.

Portal labels move between releases; the five things you need are the App ID, the
tenant id, a secret value, the messaging endpoint, and the Teams channel enabled.
Microsoft's own walkthroughs: [Register a bot with Azure][register] and
[Connect a bot to Teams][channel].

## 3 · Two Teams app packages

One package per bot, at `teams-apps/workflow/` and `teams-apps/support/`. Each is a
**flat zip** of exactly three files:

```
manifest.json
color.png     192x192
outline.png   32x32, transparent
```

In each `manifest.json`:

- `"id"` — a **new GUID for the Teams app**. This is *not* the bot App ID, and the
  two apps must not share it.
- `"bots": [{ "botId": "<that bot's Microsoft App ID>", "scopes": ["groupChat"] }]`
  — add `"team"` / `"personal"` only if you want it installable there too.
- `"isNotificationOnly": false` — the bot must be able to receive installation and
  lifecycle activities, which is how conversation references are captured.
- **Distinct** `name.short`, `name.full`, `description` and icons per bot. This is
  what makes them read as two senders rather than one.
- `"validDomains": ["<tunnel-host>"]` — for hosted content only. **Not a security
  boundary for bot activities.**

Upload: Teams → **Apps** → *Manage your apps* → **Upload an app** → *Upload a
custom app* — see [Upload your custom app][upload].

This requires **custom app upload to be enabled** for your account in Teams admin
centre (*Teams apps → Setup policies → Upload custom apps*). If that toggle is off,
nothing else in this section works, and the failure looks like a missing menu item
rather than an error.

## 4 · The dev tunnel

```powershell
devtunnel user login
devtunnel create teams-bots --allow-anonymous
devtunnel port create teams-bots -p 3978 --protocol https
devtunnel host teams-bots
```

- **Only port 3978.** Never add 3979 — that is the internal command listener and
  the whole isolation contract (C1) depends on it staying local.
- `--allow-anonymous` is required so Microsoft can reach the endpoint without a
  tunnel credential. That is precisely why **Bot Connector JWT validation is
  mandatory** on the public listener: anyone with the URL can POST to it, and the
  SDK's authentication is the only thing that rejects them.
- The host URL is printed on `host`. Put it in `TEAMS_PUBLIC_BASE_URL` **and** in
  both bots' messaging endpoints.
- Verify the syntax against your installed CLI version — these flags have changed
  between releases. Current reference: [dev tunnels CLI commands][tunnelcli].

## 5 · The HMAC secret — generated locally, not by Azure

This one is ours, shared between the Python worker and the Node gateway:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Same value in both processes. Key id is just a rotation label, e.g. `dev-1`.

## 6 · Where the values go

| Value | Gateway (`services/teams-gateway/.env`) | Python (`.env`) |
|---|---|---|
| Workflow App ID | `TEAMS_WORKFLOW_APP_ID` | — |
| Workflow secret | `TEAMS_WORKFLOW_APP_PASSWORD` | — |
| Support App ID | `TEAMS_SUPPORT_APP_ID` | — |
| Support secret | `TEAMS_SUPPORT_APP_PASSWORD` | — |
| Tenant id | `TEAMS_ALLOWED_TENANT_ID` | — |
| Tunnel URL | `TEAMS_PUBLIC_BASE_URL` | — |
| Ports | `TEAMS_PUBLIC_PORT=3978`, `TEAMS_INTERNAL_PORT=3979` | — |
| Gateway Mongo | `TEAMS_MONGO_URI` | — |
| HMAC key id | `TEAMS_HMAC_KEY_ID` | `PLATFORM_TEAMS_HMAC_KEY_ID` |
| HMAC secret | `TEAMS_HMAC_SECRET` | `PLATFORM_TEAMS_HMAC_SECRET` |
| Gateway URL | — | `PLATFORM_TEAMS_GATEWAY_URL=http://127.0.0.1:3979` |
| Enable / timeout | — | `PLATFORM_TEAMS_ENABLED`, `PLATFORM_TEAMS_REQUEST_TIMEOUT_SECONDS` |

**No bot App ID or password ever appears on the Python side** (C3). A compromised
worker must not be able to impersonate either bot.

Both `.env` and `.env.*` are gitignored **at any depth** in this repository
(`.gitignore:25` and `:31`), so `services/teams-gateway/.env` is safe. Manifests
under `teams-apps/` **are** tracked — they carry the bot App ID, which is a public
identifier, and must never carry a secret.

## 7 · Secret vs public

| Value | Secret? |
|---|---|
| App password / client secret | **Yes.** Never commit, never log, never put in Mongo or an outbox payload |
| HMAC secret | **Yes.** Environment only, at runtime |
| Microsoft App ID | No — a public identifier, and it is in the manifest |
| Tenant id | No, but treat as internal |
| Tunnel URL | No, but anyone with it can reach the public listener |

## 8 · Rotation and expiry

Client secrets expire — pick the shortest lifetime you can live with, and note the
date. When one expires the symptom is `401` from Bot Connector, which C7 maps to
`BLOCKED_EXTERNAL_DEPENDENCY`: delivery stops, the RMA is unaffected, and the
outbox row shows the reason. Rotate by adding a new secret, updating the gateway
`.env`, restarting the gateway, then deleting the old secret.

The HMAC secret rotates independently: add a new `TEAMS_HMAC_KEY_ID`, update both
sides, restart both.

---

## 9 · Three operational traps worth knowing in advance

**Re-registering the Teams channel invalidates every stored conversation
reference.** Deleting and re-enabling the Teams channel on a bot regenerates its
key pair, which invalidates the `29:…` and `a:…` conversation ids the gateway has
persisted. Every reference goes stale at once and only a fresh installation or
lifecycle activity restores them. Treat "delete the channel and re-add it" as a
destructive action, not a troubleshooting step.

**`validDomains` and the tunnel host.** A dev tunnel URL looks like
`https://<id>.devtunnels.ms`, so that is what goes in `validDomains` — again, for
hosted content only, never as a security boundary for activities.

**Permission errors on the first proactive send** are usually the service URL not
being trusted by the SDK credentials rather than a genuine permission problem;
the Bot Framework SDKs expose a trust-service-url call for exactly this. Agent A
should handle it in the gateway rather than treating it as a Teams configuration
fault.

---

## Checklist before Wave 2

- [ ] **A work or school Microsoft 365 account — not a personal one** (§0)
- [ ] `devtunnel` installed and logged in
- [ ] Two Azure Bot resources, single-tenant, Teams channel enabled
- [ ] Two App IDs, two secrets, one tenant id recorded
- [ ] Tunnel created with **only** port 3978, URL recorded
- [ ] Both messaging endpoints set to the tunnel URL
- [ ] Two Teams packages built with distinct ids, names and icons
- [ ] Custom app upload enabled for your account
- [ ] Both apps installed in **the same** target group chat
- [ ] HMAC secret generated and present on both sides

---

## References

All verified against Microsoft Learn while writing this document.

- [Prerequisites to create a Teams agent or app][prereq] — the work/school account requirement
- [Upload your custom app][upload] — sideloading procedure
- [Manage custom app policies and settings][policies] — the admin toggle that gates it
- [Publish your agent or app — overview][publish] — distribution options beyond sideloading
- [Register a Bot Framework bot with Azure][register] — creating the bot resource and App ID
- [Connect a bot to Microsoft Teams][channel] — enabling the Teams channel
- [Provision and publish a bot in Azure][provision]
- [Dev tunnels — create and host a tunnel][tunnels] and the [CLI command reference][tunnelcli]
- [Microsoft 365 Developer Program FAQ][devfaq] and [Join with a Visual Studio subscription][devvs]
- [Set up a Microsoft 365 developer sandbox][devstart]

[prereq]: https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/tools-prerequisites
[upload]: https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/deploy-and-publish/apps-upload
[policies]: https://learn.microsoft.com/en-us/microsoftteams/teams-custom-app-policies-and-settings
[publish]: https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/deploy-and-publish/apps-publish-overview
[register]: https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration?view=azure-bot-service-4.0
[channel]: https://learn.microsoft.com/en-us/azure/bot-service/channel-connect-teams?view=azure-bot-service-4.0
[provision]: https://learn.microsoft.com/en-us/azure/bot-service/provision-and-publish-a-bot?view=azure-bot-service-4.0
[tunnels]: https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started
[tunnelcli]: https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/cli-commands
[devfaq]: https://learn.microsoft.com/en-us/office/developer-program/microsoft-365-developer-program-faq
[devvs]: https://learn.microsoft.com/en-us/office/developer-program/join-with-visual-studio
[devstart]: https://learn.microsoft.com/en-us/office/developer-program/microsoft-365-developer-program-get-started
