# aMusicServerTemplate — Project Handoff

**Last updated:** 2026-08-24 (session `musicserver-usable-status`)
**Server:** `/home/taichi/repos/musicServer/aMusicServerTemplate`, branch `bare_bones`
**Run:** systemd user service `amusicserver` (`Restart=always`), Flask on :5000, live for weeks
**Goal of this phase:** finish the project to a genuinely usable status.

> The previous handoff (2026-06-10) is fully superseded — Last.fm keys are SET, the
> weekly/daily/genre mixes run on schedule, insights is live, tests are green.
> `docs/ROADMAP.md` describes the feature surface; this file is the session-state layer.

---

## Where things stand (end of 2026-08-24 session)

| Workstream | State |
|---|---|
| Usable-status implementation batch (21 tasks) | **DONE on worktree branch, unmerged** — awaiting review + merge decision |
| Code review of that branch | Running (relaunched — first run died mid-flight, see below) |
| Web setup wizard + managed Navidrome | Spec committed (`4581e4e`), awaiting user review, then plan |
| SC personal mixes | **Phase 1 COMPLETE** — endpoint contract proven live; Phase 2 unplanned |
| Chrome/AppArmor blocker | **SOLVED** (root cause + fix applied by user; chromium verified working) |

Baseline before the batch: 570 tests passed / 1 skipped. Branch after: **637 / 1**.

---

## 1. The implementation branch (biggest pending item)

Everything designed and approved this session was implemented by a subagent on:

- **Worktree:** `.claude/worktrees/agent-a746fbaaf3640c8f3`
- **Branch:** `worktree-agent-a746fbaaf3640c8f3` — 20 commits on top of `bare_bones`
  (`d8da7d0`), 23 files, +2285/−273. NOT merged, NOT pushed.
- **Plan:** `docs/superpowers/plans/2026-08-24-usable-status-batch.md` (21 tasks, committed `d8da7d0`)

What it contains (all designs were approved in-chat, no spec docs by user choice):

1. **Dedup dry-run review** — `groups_detail` in report, `POST /library/dedup/delete`
   (song_dir containment check), two-step Scan→review→Delete UI. Removes the old
   delete-immediately footgun.
2. **Mixes editor completion** — manual-seed artist chips + collapsed per-mix
   "Advanced" quality section (empty = inherit global).
3. **Audio preview** — `progressive_url` captured on SC tracks, `/sc/preview` resolves
   the progressive MP3 server-side (HLS is unplayable in `<audio>`), `/preview`
   hardcoded yt-dlp path fixed, ▶ buttons + shared mini-player above the nav.
4. **Share UI** — Library "Share" card (playlist→code via `/share/code`; paste box →
   `/share/parse` → import with progress). `/share/import` now redirects to
   `/?share=<d>#library` (was: dead `/explore`).
5. **Batch import card** — Spotify playlist URL / Exportify CSV (browser-parsed) /
   plain text → `/import/tracks` + `/import/status` progress (first UI consumer).
6. **Merge-aware playlists** (`discover/playlist_sync.py`) — generated playlists are
   written via Subsonic API with an engine-owned ledger: user additions never touched,
   user deletions respected, cap evicts engine tracks only. Wired into both the mix
   engine and the follow runner. One-time migration per playlist: API-delete old,
   rename `.m3u` → `.m3u.bak`, seed API playlist. **Live spike PASSED** (no ghost
   playlists on this Navidrome; scan ~3 s). Motivation: Symfonium phone edits used to
   be overwritten; user will now edit server playlists directly.

Deviations from plan were cosmetic (CSS class names, a test helper shape) — listed in
the implementer's report, none change semantics.

**Code review: COMPLETE, all findings fixed.** The high-effort review confirmed 10
findings — headlined by a silent playlist-wiping bug (bracket-indexed `songId[i]`
params that Navidrome ignores; the unit tests had encoded the same bug) plus a
destructive migration ordering, a scan race, pending/ledger loss paths, an SSRF hole
in `/sc/preview`, a never-completing import progress poll, a blocking `/follow/run`,
and the import path's delete-and-recreate playlist write. All 10 fixed on the branch
(commits `93c4cec`..`8aa9194`), independently verified: **659 passed / 1 skipped**.
Seven lower-priority cleanup items (schema duplication, unbounded pending queue, dead
`_existing_playlist_basenames` helper, duplicated m3u sanitizer) were cut by the
review's findings cap — candidates for a later `/simplify` pass, not merge blockers.

**Merge/deploy (user decision, not taken yet):** merge branch → `bare_bones`, restart
`amusicserver`. First scheduled run of each mix after deploy performs the m3u→API
migration (renames the mix `.m3u`s to `.m3u.bak`). User should then delete stale
phone-local Symfonium playlist copies and edit server playlists directly.
Note: the worktree contains an (gitignored, uncommitted) copy of `config.json` with
live credentials — do not commit it; remove worktree after merge.

## 2. SC personal mixes — Phase 1 complete

- **AppArmor root cause:** `/etc/apparmor.d/chromium` + `chrome` were missing
  `flags=(unconfined)` → silent default-deny → the historic
  `libglib Permission denied` / ld.so.cache EACCES. User applied the fix (sed +
  `apparmor_parser -r`); chromium headless verified working. Memory file
  `project-sc-mixes-chrome-blocker.md` updated.
- **Headless SC login still CAPTCHA-blocked** → token is a provisioned secret now.
  A valid `sc_oauth_token` is in `config.json` (grabbed manually 2026-08-24, account
  `user352647366`). SC web tokens live weeks–months; on 401, re-provision.
- **Endpoint contract proven** (full dump: `logs/sc_mix_discovery.json`):
  `GET /mixed-selections` → selections `made-for-you` (Weekly Wave `…weekly:<uid>`,
  Daily Drops `…new-for-you:<uid>`) and `your-moods` (Your Mix 1–6) →
  `GET /system-playlists/<urn>` (tracks come as id-only stubs) →
  `GET /tracks?ids=` hydration (batch ~20). Hydrated tracks fit `_track_from_raw`.
- **Spec:** `docs/superpowers/specs/2026-07-15-soundcloud-personal-mixes-design.md`
  amended (`7a0ab45`, `597da4f`): three-layer token provisioning (extension cookie
  bridge primary → headless login opportunistic → manual paste), new
  `POST /sc/oauth_token` route, Phase 2 writes via `playlist_sync` (NOT m3u snapshots).
- **Next:** Phase 2 plan (fetch module, scheduler, settings group, extension bridge) —
  after the batch merges, since it builds on `playlist_sync`.

## 3. Web setup wizard + managed Navidrome

Spec: `docs/superpowers/specs/2026-08-24-web-setup-wizard-navidrome-design.md`
(committed `4581e4e`). Approved in design discussion; **written spec awaits user
review**, then: Opus planning agent → implementation. Highlights: 5-step first-run
wizard in the web UI; `navidrome_mgr/` module auto-installs Navidrome on all OSes
(download+checksum, toml, child-process supervision, admin bootstrap); first
implementation task = verify Navidrome's admin-creation endpoint against a real
release.

## 4. Smaller known items (approved direction, not yet done)

- `logs/server.log` is 164 MB — needs rotation (logging.handlers or logrotate).
- systemd unit: `StartLimitIntervalSec` sits in `[Service]` (ignored; belongs in `[Unit]`).
- Two venvs exist (`venv/` and `.venv/` — systemd uses `.venv`, tests were run with `venv`); consolidate.
- Untracked-but-referenced docs should be committed: `docs/superpowers/plans/2026-06-10-*`,
  `specs/2026-06-10-library-management-setup-wizard-design.md`, `specs/2026-06-10-sc-spotify-ui-share-design.md`,
  `tests/discover/test_daily.py` (note: implementer found it absent from the branch —
  it exists only untracked in the main tree).
- No route auth (documented LAN-only stance) — deliberate, revisit if ever exposed.

---

## How to resume

```bash
cd /home/taichi/repos/musicServer/aMusicServerTemplate

# main-tree tests
venv/bin/python -m pytest tests/ -q

# implementation branch
cd .claude/worktrees/agent-a746fbaaf3640c8f3
git log --oneline bare_bones..HEAD
/home/taichi/repos/musicServer/aMusicServerTemplate/venv/bin/python -m pytest tests/ -q

# deploy after merge (user call)
systemctl --user restart amusicserver && journalctl --user -u amusicserver -f
```

**Recommended order:** review findings → fix → merge+deploy → verify migration on one
mix → wizard plan → SC mixes Phase 2 plan → ops one-liners.
