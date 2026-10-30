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
- [ ] Run selected and full-catalog live acceptance in GitHub Actions.
- [ ] Install verified code in main and confirm daily workflow is enabled.
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
