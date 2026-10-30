# HomeRadar daily update implementation

Approved architecture: public GitHub standard runner runs Chromium daily at 00:17 UTC;
WordPress REST is the full company registry; validated per-organization legacy JSON
is delivered over HTTPS only when Alexander configures the receiver URL and secret.
Collection runs before receiver setup; raw data stays ephemeral and private.

- [x] Restore the real Chromium collector; keep offline replay and atomic validation.
- [x] Replace sitemap-only discovery with complete paginated published-company REST registry.
- [x] Introduce one canonical collection coordinator and current-run manifest.
- [x] Add isolated, idempotent HTTPS delivery with strict typed acknowledgment.
- [x] Add schedule/manual controls, secret isolation and safe artifacts.
- [x] Document Alexander's receiver contract and operational ownership.
- [x] Complete integrated offline tests and independent security/spec review.
- [x] Run selected and full-catalog live acceptance in GitHub Actions.
- [x] Install verified code in main and confirm daily workflow is enabled.
- [ ] Observe two actual cron executions (cannot substitute manual runs).
- [ ] Alexander installs receiver and confirms real staging/production import.

Never treat absent configuration, missing source fields, partial catalog results,
HTTP success without matching receipt, or local test success as site acceptance.
No site modifications or receiver credentials are currently available.

Selected cloud run 41092805300 verified actual Chromium, current-run manifests and
dry-run delivery; it exposed anonymous ISO timestamp aliases (Z vs .000Z).
The fix canonicalizes instants only for identity, preserves original date values,
and retains ambiguity rejection. Local reproduction1098497214 now exports30 rather
than rejecting31 duplicate rows. Regression tests cover profiles, anonymous reviews,
invalid/different dates, old cache aliases, and ambiguous candidates.

Full run [41094343671](https://github.com/Hmmir/homeradar-yandex-parser/actions/runs/41094343671)
at code4fd11aa:224 offline tests passed on Ubuntu; all258WordPress pages checked,
221uniqueYandex organizations attempted;184passed (6081reviews,5526avatars,4566replies).
All remaining37organizations were explicitly rejected, not published or silently excluded.
Registry mapping errors:32company pages without an explicitYandex link,4redirect cycles.
Delivery preflight succeeded in dry-run mode; site_updated=false and noPOSTs occurred.
This is a completed partial acceptance run, not proof that every source card is supported.

2026-10-31 installation gate: local224tests passed again (oneWindows symlink privilege
skip; corresponding containment test passes independently); Ruff and diff checks passed.
GitHub repository secrets/variables are empty. OnlyownerHmmir currently has access.
Public-code collection-only rollout is authorized; Alexander must still implement/configure
the receiver, and two real scheduled executions must be observed after installation.

Installed code602ad77 on main. Daily workflow402249100 is active with cron17 0 * * *
(03:17Moscow). Actual production-workflow manual run
[41130822695](https://github.com/Hmmir/homeradar-yandex-parser/actions/runs/41130822695)
passed all224offline tests and7/7selected organizations (141reviews), including three
previous anonymous-date failures and three earlier transient source mismatches.
The delivery step ran without dry-run override, confirmed absent receiver configuration,
and returnedcollection_only/not_configured withsite_updated=false andzeroPOSTs.
Artifact inspection confirmed summaries only, no rawexports ormanifest.
This proves installed manual execution; it does not substitute two future cron runs
or Alexander's actual WordPress import acceptance.
