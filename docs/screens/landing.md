# Platform landing

**Route** `/` · **Capability** none (the launcher itself) ·
**Component** `frontend/src/domains/PlatformLanding.tsx`

## Purpose

The launcher. An operator arriving with no particular destination decides here
which domain answers their question. It is the only screen whose job is to send
you somewhere else.

## UI regions

**Domain cards** — one per entry in `DOMAINS` (`frontend/src/domains/registry.ts`).
Each card shows the domain's icon, name, `purpose` and, when set, a status badge.

`purpose` and `description` are deliberately different strings. `description`
says what a domain *contains*; `purpose` says what it is *for*. A card is read by
someone deciding where to go, not by someone already there — so the card shows
`purpose`.

A card is rendered only if the principal holds the domain's `requires`
capability.

## The card status model

`DomainDefinition.status` is optional and currently has one value:

| Value | Meaning |
|---|---|
| *(absent)* | The domain has a working backend surface. |
| `"NO BACKEND YET"` | **No** backend surface exists for this domain yet. The screen is a shell. |

The badge is not a quality signal and not a maturity rating. It answers exactly
one question: will anything I do here reach a server?

It is removed the moment a backend surface lands. Operations carried the badge
until `/api/cases`, `/api/config/adoption` and the support surface backed its
Cases section and `/api/returns` backed Return sessions; keeping the badge over
two working screens would have been the same lie in the other direction. The
platform-health half of Operations — graph generations, workers, outbox — still
has no API, so that domain now promises only what it has rather than carrying a
blanket badge.

## Actions

| Action | Effect | Reversible |
|---|---|---|
| Click a card | Client-side navigation to the domain root | Yes — browser back |

No API call, no side effect, no audit event.

## Backend APIs consumed

| Method | Path | Used for |
|---|---|---|
| `GET` | `/api/principal` | The caller's capabilities, which decide card visibility |
| `GET` | `/api/runtime-config` | Shell boot configuration |

`/api/runtime-config` is the last versionless-surface exception the shell needs to
boot. It lives in `bootstrap/`, where the target design places it, and
`frontend/src/api/noVersionedPaths.test.ts` asserts the absence of any other —
because describing a leftover in a README is exactly what let it survive three
deletion waves.

## Live-state behaviour

Static. Capabilities are fetched once on shell mount and cached by React Query.
There is no polling; a capability grant made while the tab is open requires a
reload.

## Loading, error and empty states

| State | Renders |
|---|---|
| Loading | Card skeletons |
| Error on `/api/principal` | An error panel. **No cards are shown** — an unknown principal is not the same as a principal with no capabilities, and rendering an empty grid would suggest the account has no access. |
| Empty (principal holds no domain capability) | An explicit "no domains are available to this account" message |

## Persistence and data source

None. The domain list is a compile-time constant in `registry.ts`; only
visibility is server-driven.

## Audit effects

None.

## Configuration dependencies

None. Adding or removing a domain is a frontend code change, not configuration.
This is deliberate: a domain is a screen, and a screen that can be configured
into existence cannot be typechecked.

## Known constraints

- Capability changes require a reload to take effect on this screen.
- The card list cannot be reordered or hidden per-operator.
