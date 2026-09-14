# aMusicServerTemplate — Project Handoff

**Last updated:** 2026-09-14 (session `library-tools-audit`)
**Server:** `/home/taichi/repos/musicServer/aMusicServerTemplate`, branch `bare_bones`
**Run:** systemd user service `amusicserver` (`Restart=always`, `.venv/`), Flask on :5000.
Web UI is reached through the Raspberry Pi at 192.168.178.149 (WireGuard / LAN), so
every request in `logs/server.log` shows that IP.
**Next session's job:** make the three Library utilities (De-duplicate, Enrich
metadata, Repair library) work as intended. Findings and a proposed order are below.

> Supersedes the 2026-08-24 handoff. The usable-status batch it tracked is merged and
> deployed (`e0d5128`). Its still-open items are carried forward in sections 5–7.

---

## Where things stand

| Workstream | State |
|---|---|
| Search ▶ YouTube preview | **FIXED, confirmed on the user's phone, committed** (section 1) |
| De-duplicate | **Broken.** Misses real duplicates; scheduled auto-delete removed 4 real songs — auto-delete now OFF (section 0, 2) |
| Enrich metadata | Works, low yield. Last run 2026-08-08 (section 3) |
| Repair library | Runs, **has repaired 0 files in every run ever** (section 4) |
| SC personal mixes Phase 2 | Unplanned (carried, section 5) |
| Web setup wizard + managed Navidrome | Spec awaits user review (carried, section 6) |

Tests: **680 passed / 1 skipped** (`venv/bin/python -m pytest -q`).

---

## 0. Dedup auto-delete — disarmed 2026-09-14

- `config.json:28` had `"dedup": {"auto_delete": true}` (`config.example.json` ships `false`).
  **Set to `false` on 2026-09-14** (config.json is gitignored — live-only change; no restart
  needed, `_run_dedup_once` re-reads config every run).
- `_dedup_scheduled_loop` (`sWebExt/py_server/server.py:944`, started at `:2565`) sleeps
  `interval_hours` (24) **after each service start**, then calls `_run_dedup_once()`
  which honours `auto_delete`. Last restart: Mon 2026-09-14 16:35:06 CEST → next run ~24 h later.
- The Web UI "scan" button uses `/library/dedup/report` (forced dry run) — safe.
  `POST /library/dedup/run` and the scheduled loop are not.
- **Data loss, 2026-09-08 21:03** (`logs/server.log`): title-only keys grouped different
  songs and deleted the non-kept copy. Gone for good (ext4 NAS, no snapshots, no copies found):
  - `21 Savage/05 Nightmare.mp3` (Offset & Metro Boomin) — "kept" SHADXWBXRN "NIGHTMARE"
  - `Ye/01 Intro.mp3` (Kanye West) — "kept" 21 Savage "Intro"
  - `21 Savage/09. Numb.mp3` — "kept" XXXTENTACION "NUMB"
  - `A$AP Rocky/13. Changes.mp3` — "kept" XXXTENTACION "changes"
- **Still open:** re-acquire the 4 songs; decide in the Dedup redesign whether auto-delete
  should exist at all.

---

## 1. YouTube preview fix (committed)

**Symptom:** ▶ on YouTube results in Search did nothing on the phone; SoundCloud ▶ worked.

**Root cause (verified):** yt-dlp 2025.11.12 (`.venv`) had no JS runtime and was too old
for current YouTube. Its googlevideo links (`c=ANDROID`) served only the first ~1 MB;
any whole-file, open-ended, or mid-file range returned **403**. Browsers request
`bytes=0-` / large ranges, so `<audio>` failed silently (the player swallows errors).
SoundCloud's CDN accepts full ranges, hence SC worked. A chunked server proxy would
NOT have helped — mid-file chunks were refused too.

**Fix (verified live, confirmed by user on phone):**
- `yt-dlp[default]==2026.8.19` installed in **both** `.venv/` (service) and `venv/`
  (tests); `requirements.txt` pin updated (file uses CRLF). Links are now `c=VISIONOS`
  and serve `bytes=0-` and mid-file ranges. No `--js-runtimes node` flag needed
  (Node v22 is installed if ever required; deno is not).
- `scripts/sTownload/script_web.py` and `scripts/sTownload/app.py`: removed the forced
  `extractor_args.youtube.player_client` (`android,web` / `android`). With new yt-dlp that
  setting yields "Requested format is not available", i.e. every download would fail.
  Both files are CRLF — keep it that way (diff is one deleted line each).
- `sWebExt/py_server/server.py` `/preview`: format `bestaudio[ext=m4a]/bestaudio/best`
  (was `bestaudio/best` → WebM/Opus). Not the root cause, kept for iOS compatibility.
- Tests: `tests/server/test_routes.py::test_preview_route_prefers_m4a_audio_over_webm`,
  `tests/server/test_script_web_download.py::test_download_does_not_force_a_youtube_player_client`,
  `::test_legacy_app_get_song_does_not_force_a_youtube_player_client`.
- Verified after restart: preview link 206 `audio/mp4` for full range and a mid-file chunk;
  `/yt/search` 10 results in ~1.6 s; a real `download_url()` produced a 6.9 MB mp3 (scratch dir).

**Committed** on `fix/youtube-preview`, merged into `bare_bones`. If YouTube breaks again,
update yt-dlp first — YouTube changes clients often.

---

## 2. De-duplicate — why it finds nothing

Code: `library/scanner.py`, `library/dedupe.py`, `_run_dedup_once` (`server.py:923`),
routes `server.py:1295-1340`, UI `web/static/app.js:990-1100`.

Findings (verified against the live library, `/mnt/nas1/media/music`):
1. **MP3 only.** `scanner.scan` skips non-`.mp3` (`library/scanner.py:29`). Library holds
   2228 mp3, 493 flac, 32 m4a, 1 mp4 — FLAC album rips are invisible.
2. **Exact casefolded title is the whole key** (`_make_record`, `library/scanner.py:13`).
   YouTube downloads are tagged `title="XXXTENTACION - SAD! (Audio)"`, artist = uploader
   channel; the album copy is `title="SAD!"`. They never collide. Unrelated songs with the
   same title *do* collide → the 2026-09-08 deletions. Today 0 exact collisions remain.
3. `_pick_keep` (`library/dedupe.py:18`) prefers "has artist+title tags", then oldest mtime —
   no notion of lossless vs lossy, album folder vs root download, bitrate.

Audit heuristic (section 8 script: cleaned title from tag or filename, "Artist - " prefix
stripped, duration ±3 s, all formats) found **39 candidate groups / 78 files**:

| Kind | Groups |
|---|---|
| MP3 download (root) vs FLAC album copy (artist folder) | 18 |
| MP3 vs MP3 (cross-dir 7, same-dir 13) | 20 |
| MP3 vs MP4 | 1 |

At least one is a false positive (Nakama "DIA DELÍCIA" vs "MENTE MÁ", 75 s vs 76 s) —
**artist comparison is mandatory** in any real matcher.

Existing tests that encode today's behaviour and will need rewriting:
`tests/library/test_scanner.py::test_scan_ignores_non_mp3_files`,
`::test_make_record_uses_id3_key_when_tags_present`, and the `find_groups` / `_pick_keep`
tests in `tests/library/test_dedupe.py`.

Proposed direction (needs brainstorming + user sign-off before code):
- Read all audio formats (mutagen is available; eyed3 is mp3-only).
- Key = normalized artist + normalized title, where "Artist - Title" in the title field and
  noise suffixes (reuse `library/tagger.py` suffix list) are stripped first; confirm with
  duration within a few seconds.
- Keep preference: lossless > artist/album folder > complete tags > higher bitrate.
- Report-only by default; remove or hard-gate auto-delete.
- **Open question:** deleting a file that generated playlists reference — check how
  `discover/playlist_sync.py`'s ledger and Navidrome react before deleting anything.

---

## 3. Enrich metadata — works, low yield

Code: `library/enrich.py`, `library/mbmeta.py`, `library/coverart.py`,
`_run_enrich_once` (`server.py:651`), UI `app.js:904-945`.
Logic is sound: fills only empty fields (per-field `only_missing`), MusicBrainz score ≥ 90,
one MB resolve per file, progress polling works.

Run history (`logs/server.log`; status is in-memory, so the UI says "not run yet" after any restart):

| Date | Files | Enriched | genre | year | album | album_artist | mbids | cover |
|---|---|---|---|---|---|---|---|---|
| 2026-06-14 | 793 | 43 | 28 | 0 | 3 | 31 | 31 | 0 |
| 2026-06-16 | 904 | 51 | 51 | 0 | 0 | 13 | 13 | 0 |
| 2026-06-17 | 989 | 56 | 56 | 0 | 0 | 4 | 4 | 0 |
| 2026-07-11 | 1245 | 114 | 114 | 0 | 0 | 18 | 18 | 0 |
| 2026-07-13 | 1265 | 9 | 9 | 0 | 0 | 2 | 2 | 0 |
| 2026-08-08 | 1617 | 160 | 72 | 0 | 1 | 8 | 93 | 0 |

Current tag gaps (audit, 2026-09-14):

| Gap | mp3 (2228) | flac (493) | m4a (32) |
|---|---|---|---|
| No album | 1564 | 0 | 0 |
| No genre | 827 | 30 | 14 |
| No year | 119 | 0 | 0 |
| No cover | 16 | 30 | 0 |
| No MusicBrainz IDs | 2067 | 493 | 32 |

Findings:
1. **MP3 only** (uses `scanner.scan`, `library/enrich.py:84`) — FLAC/M4A gaps never filled.
2. **Uploader-as-artist breaks lookups.** 670 of the 1210 mp3s whose title is "X - Y" have
   an artist tag that isn't X (e.g. `UKF Drum & Bass` / "Sub Focus - Solar System",
   `Houseum`, `WORLDSTARHIPHOP`). MB search `artist:"UKF Drum & Bass" AND recording:"Sub Focus
   - Solar System"` can't match, and the Last.fm genre fallback (`get_artist_tags`) tags the
   *channel*, which can write wrong genres.
3. **Unexplained:** `year` and `cover_art` were 0 in every run; `album` ≤ 3 per run while
   1564 files lack an album, yet `mbids` reached 93 on 2026-08-08. MB matches happen but
   album isn't written — check the recording-search `releases` payload / `_pick_release`
   (`library/mbmeta.py`). Year/cover may simply be rare gaps (119 / 16) — verify.
4. No Navidrome rescan after writing tags; `Subsonic.start_scan()` exists
   (`discover/subsonic.py:260`, used at `server.py:640`).

---

## 4. Repair library — repairs nothing

Code: `library/repair.py`, `_run_repair_once` (`server.py:861`), UI `app.js:946-988` and `:1540-1545`.

Every run on record (2026-06-12 … 2026-08-08, up to 1617 files) reports
`repaired_stage1/2/3 = 0`. Why:
1. It only touches mp3s with an **empty artist that still have a title**
   (`library/repair.py:129`). All 110 artist-less mp3s also have no title → all skipped.
2. The real metadata problem — **uploader-as-artist (670 files)** — is out of its scope.
3. Stages 2/3 search Last.fm / MusicBrainz **by title only**; generic titles ("Intro")
   would get an arbitrary artist.
4. `_SEPARATOR_RE` (`library/repair.py:14`) makes spaces optional, so "Jay-Z Song" would
   split into "Jay" / "Z Song". Tests only use spaced separators, so tightening is safe.
5. UI: `pollRepair` (`app.js:962`) never sees `running` (`_run_repair_once` doesn't set it),
   stops after one 2 s poll and shows "not run yet"; on completion both `app.js:978` and
   `:1543` read `s.fixed`, which the server never returns.

Proposed direction (needs user sign-off — it rewrites tags on hundreds of files):
redefine Repair as "fix uploader-as-artist": when title is "A - T" and the artist tag
doesn't occur in A, set artist = A, title = T with noise suffixes stripped (optionally keep
the uploader in a TXXX frame). Dry-run report + explicit apply, like the dedup review flow.
Handle the 110 fully untagged files from the filename.

---

## Suggested order for the library work

1. Section 0 safety decision (auto-delete off, re-acquire 4 songs).
2. **Repair** redesign — clean artist/title is the input everything else needs.
3. **Enrich** — re-run after repair; investigate the album/year/cover zeros; add FLAC/M4A;
   trigger a Navidrome scan.
4. **Dedup** — artist-aware, all formats, report-only.

Use `superpowers:brainstorming` for 2 and 4 (behaviour changes); TDD throughout.

---

## 5. SC personal mixes — Phase 1 complete (carried from 2026-08-24, not re-verified)

- Headless SC login is CAPTCHA-blocked → `sc_oauth_token` in `config.json` is a provisioned
  secret (grabbed 2026-08-24); on 401, re-provision.
- Endpoint contract proven (`logs/sc_mix_discovery.json`): `GET /mixed-selections` →
  `GET /system-playlists/<urn>` → `GET /tracks?ids=` hydration.
- Spec: `docs/superpowers/specs/2026-07-15-soundcloud-personal-mixes-design.md`
  (Phase 2 writes via `playlist_sync`).
- **Next:** Phase 2 plan (fetch module, scheduler, settings group, extension cookie bridge).

## 6. Web setup wizard + managed Navidrome (carried)

Spec `docs/superpowers/specs/2026-08-24-web-setup-wizard-navidrome-design.md` (`4581e4e`)
awaits user review → plan → implementation. First task: verify Navidrome's
admin-creation endpoint against a real release.

## 7. Smaller known items (carried, plus this session)

- User to-do from 2026-08-24: edit a synced playlist in Symfonium, confirm it survives the
  next mix run, then delete phone-local playlist copies.
- `logs/server.log` needs rotation (164 MB on 2026-08-24).
- systemd unit: `StartLimitIntervalSec` is in `[Service]` (ignored; belongs in `[Unit]`).
- Two venvs (`.venv/` service, `venv/` tests) — consolidate. Both now on yt-dlp 2026.8.19.
- Untracked files to commit or delete: `docs/superpowers/plans/2026-06-10-*`,
  `docs/superpowers/specs/2026-06-10-library-management-setup-wizard-design.md`,
  `tests/discover/test_daily.py`, `discover_state.json`, `logs/`.
- The shared audio `Player` (`app.js:35`) swallows playback errors — surface them on the
  button so the next silent failure is visible.
- No route auth (LAN/WireGuard-only stance) — deliberate.

---

## 8. Library audit script

Read-only; walks the library with mutagen, prints tag-gap counts per format and candidate
duplicate groups, and writes all groups to the JSON path given as argument. ~1 min over the NAS.

```bash
venv/bin/python /path/to/libaudit.py /tmp/dupgroups.json   # save the block below first
```

```python
import os, re, json, sys, collections
import mutagen
ROOT="/mnt/nas1/media/music"
EXT={".mp3",".flac",".m4a",".mp4"}
NOISE=re.compile(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|visuali[sz]er|hd|hq|4k|prod\.?|remaster)[^\)\]]*[\)\]]", re.I)
def norm(s):
    s=(s or "").casefold()
    s=NOISE.sub("",s)
    s=re.sub(r"\.(mp4|mp3|webm)$","",s)
    s=re.sub(r"^\d{1,3}[\.\s_-]+","",s)          # leading track number
    s=re.sub(r"[_⧸｜|]"," ",s)
    s=re.sub(r"\s*(feat\.?|ft\.?)\s.*$","",s)
    s=re.sub(r"[^\w\s]","",s)
    return re.sub(r"\s+"," ",s).strip()
recs=[]
for d,_,fs in os.walk(ROOT):
    for f in fs:
        p=os.path.join(d,f); e=os.path.splitext(f)[1].lower()
        if e not in EXT: continue
        a=t=al=g=y=None; dur=None; cover=False; mbid=False
        try:
            m=mutagen.File(p, easy=True)
            if m is not None:
                g1=lambda k: (m.get(k) or [None])[0]
                a,t,al,g,y=g1("artist"),g1("title"),g1("album"),g1("genre"),g1("date")
                mbid=bool(m.get("musicbrainz_trackid") or m.get("musicbrainz_artistid"))
                dur=round(m.info.length) if m.info else None
            mf=mutagen.File(p)
            if mf is not None:
                if e==".mp3": cover=any(k.startswith("APIC") for k in (mf.tags or {}).keys())
                elif e==".flac": cover=bool(mf.pictures)
                elif e in (".m4a",".mp4"): cover="covr" in (mf.tags or {})
        except Exception as ex:
            pass
        recs.append(dict(p=p[len(ROOT)+1:],ext=e,a=a,t=t,al=al,g=g,y=y,dur=dur,cover=cover,mbid=mbid,size=os.path.getsize(p)))
print("files",len(recs), collections.Counter(r["ext"] for r in recs))
for e in sorted(EXT):
    rs=[r for r in recs if r["ext"]==e]
    if not rs: continue
    c=lambda k: sum(1 for r in rs if not r[k])
    print(f"{e}: n={len(rs)} no_artist={c('a')} no_title={c('t')} no_album={c('al')} no_genre={c('g')} no_year={c('y')} no_cover={c('cover')} no_mbid={c('mbid')} zero_byte={sum(1 for r in rs if r['size']==0)}")
mp3=[r for r in recs if r["ext"]==".mp3"]
print("mp3 no_artist & title has ' - ':", sum(1 for r in mp3 if not r["a"] and r["t"] and re.search(r"\s[-–—]\s", r["t"])))
print("mp3 no_artist & title has bare hyphen (no spaces):", sum(1 for r in mp3 if not r["a"] and r["t"] and re.search(r"\S-\S", r["t"]) and not re.search(r"\s[-–—]\s", r["t"])))
print("mp3 artist ends with ' - Topic' or looks like channel:", sum(1 for r in mp3 if r["a"] and r["a"].endswith(" - Topic")))
# what the current dedup would key on (mp3 only, title or filename)
k=collections.Counter((r["t"] or os.path.splitext(os.path.basename(r["p"]))[0]).strip().casefold() for r in mp3)
print("current-dedup mp3 title collisions:", sum(1 for v in k.values() if v>1))
# candidate true dupes: normalized title from tag or filename (strip "Artist - " prefix variant), duration within 3s
def keys(r):
    base=os.path.splitext(os.path.basename(r["p"]))[0]
    out=set()
    for s in (r["t"], base):
        if not s: continue
        n=norm(s); out.add(n)
        if " - " in s or " – " in s:
            out.add(norm(re.split(r"\s[-–—]\s",s,maxsplit=1)[1]))
    return {x for x in out if len(x)>=3}
idx=collections.defaultdict(list)
for i,r in enumerate(recs):
    for kk in keys(r): idx[kk].append(i)
pairs=set()
for kk,ids in idx.items():
    ids=sorted(set(ids))
    for x in range(len(ids)):
        for y in range(x+1,len(ids)):
            A,B=recs[ids[x]],recs[ids[y]]
            if A["dur"] and B["dur"] and abs(A["dur"]-B["dur"])<=3:
                pairs.add((ids[x],ids[y]))
# union-find groups
par=list(range(len(recs)))
def f(x):
    while par[x]!=x: par[x]=par[par[x]]; x=par[x]
    return x
for x,y in pairs: par[f(x)]=f(y)
grp=collections.defaultdict(list)
for x,y in pairs: pass
for i in {i for pr in pairs for i in pr}: grp[f(i)].append(i)
groups=sorted(grp.values(), key=lambda g:-len(g))
print("candidate true-dup groups (title~ + duration±3s, all formats):", len(groups), "files involved:", sum(len(g) for g in groups))
cat=collections.Counter()
for g in groups:
    exts=tuple(sorted({recs[i]['ext'] for i in g})); dirs=len({os.path.dirname(recs[i]['p']) for i in g})
    cat[(exts, 'same-dir' if dirs==1 else 'cross-dir')]+=1
print("group kinds:", dict(cat))
json.dump([[recs[i] for i in g] for g in groups], open(sys.argv[1],"w"), ensure_ascii=False, indent=1)
for g in groups[:25]:
    print("--")
    for i in g:
        r=recs[i]; print(f"   {r['dur']}s {r['ext']} a={r['a']!r} t={r['t']!r}  [{r['p']}]")
```

---

## How to resume

```bash
cd /home/taichi/repos/musicServer/aMusicServerTemplate
git status --short
venv/bin/python -m pytest -q            # expect 680 passed / 1 skipped
grep -n auto_delete config.json         # section 0
systemctl --user restart amusicserver && journalctl --user -u amusicserver -f
```

Memory: `project-library-tools-audit.md` in the Claude project memory mirrors sections 0–4.
