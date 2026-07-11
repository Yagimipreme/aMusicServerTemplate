# Genre Tag Preview & Validation — Design Spec

**Date:** 2026-07-11
**Status:** Approved for planning
**Scope:** Batch B of three (A = search polish + SC browse, merged `ae46889`; C = SC account recommendations, deferred)

## Problem

Genre-mode mixes seed artists via Last.fm's `tag.getTopArtists(tag)` with the genre string passed verbatim (`discover/seeds.py:17`). The genre field in the mix editor is free text (`prompt('Genre:')`, `web/static/app.js` mix editor) with no validation, preview, or disambiguation. Tag choice is guesswork: the user typed "dub" for a dubtechno weekly mix and got reggae, because on Last.fm the tag "dub" *is* the reggae-lineage genre. The downstream genre gate (`filter_artists_by_genre`, substring match) cannot rescue this — reggae-dub artists are legitimately and prominently tagged "dub".

The fix belongs at **input time**: show the user what a tag actually means on Last.fm before it is saved, and help them find the right tag.

## Design decisions (settled during brainstorming)

- **Input-time preview**, not seed-time automatic correction. Deterministic, no magic.
- **Suggestions from both sources:** local vocabulary autocomplete while typing (Last.fm has no tag-search endpoint) + Last.fm similar-tags row in the preview.
- **Warn, but allow:** suspect tags get a visible warning state; saving is never blocked.
- Rejected alternatives: validation at `/mixes` save time (save latency, Last.fm outage breaks saving, feedback far from the decision point); browser-direct Last.fm calls (exposes API key).

## Components

### 1. Tag preview function (`lastfm/` package)

`get_tag_preview(client, tag) -> dict` wrapping three Last.fm API calls:

- `tag.getInfo` → `taggings` (total) and `reach` counts. Unknown tags return 0s.
- `tag.getTopArtists` (limit 10) → artist names.
- `tag.getSimilar` → related tag names. This endpoint is often empty in practice; an empty result is normal, not an error.

Returns `{"tag": <normalized: stripped input>, "taggings": int, "reach": int, "top_artists": [str, …], "similar": [str, …]}`. Individual sub-call failures degrade to empty values for that field rather than failing the whole preview; only total Last.fm failure raises.

### 2. Preview route

`GET /genres/preview?tag=<text>` in `sWebExt/py_server/server.py`:

- 400 `{status:"error"}` when `tag` is missing/blank.
- `{status:"unavailable", reason:"lastfm_api_key not configured"}` (HTTP 200) when no Last.fm key — same degraded-status pattern as the `/sc/*` routes.
- `{status:"ok", tag, taggings, reach, top_artists, similar}` on success; HTTP 500 `{status:"error", error}` on upstream exception.
- In-memory TTL cache (dict keyed by casefolded tag, TTL 1 hour, no size persistence) so repeated previews don't hammer the API.

### 3. Vocabulary route

`GET /genres/vocab` returns `{status:"ok", "tags": [str, …]}` — deduplicated (casefold), alphabetically sorted merge of:

- **Insights DB:** all tag names from `artist_tags.tags_json` (every Last.fm tag of every scrobbled artist). Missing DB / empty table → contributes nothing, no error.
- **Library genres:** Navidrome via the existing Subsonic `get_genres` (as used by the genre-profile bootstrap). Missing creds / Navidrome down → contributes nothing, no error.

If both sources are empty, `tags` is `[]` and the UI simply has no autocomplete. Response cached in memory with a 10-minute TTL — the vocabulary changes slowly.

### 4. Mix editor UI (`web/static/app.js`)

Replaces the `prompt('Genre:')` chip-add flow in the mix editor:

- **Inline input** in the genre chips row. Typing ≥ 2 chars filters the vocab list (fetched lazily once per editor session) into a dropdown of up to 8 suggestions; Enter or click selects.
- **Preview card** below the input once a tag is entered/selected: taggings count, the top-10 artist names, and the similar tags as clickable chips (clicking one swaps it into the input and re-previews). The card is where "dub" visibly becomes King Tubby & Lee Perry instead of Deepchord.
- **Add button** confirms the tag as a genre chip on the mix.
- **Warning state:** a chip renders amber with a `⚠` marker when its preview shows `taggings < 100` **or** `top_artists` empty. Tooltip: "barely used on Last.fm — check the preview". Warning is display-only; save behavior is unchanged.
- **Existing chips** on saved mixes are lazily validated when the editor opens (one cached preview call per chip) and get the same warn state — so the current `"Dub-Techno"` chip immediately reveals whether that exact hyphenated tag is healthy.
- While Last.fm is unavailable, the preview card shows a muted "preview unavailable" note; chips render neutral (no warn state without data).

### 5. Explicitly out of scope

- No changes to the seed pipeline (`genre_seed_artists`) or the genre gate (`filter_artists_by_genre`) — substring gating is adequate once tags are correct.
- No persistence of validation results in `config.json`.
- No seed-time drift guard (candidate later phase if preview alone proves insufficient).
- No blocking of any save.

## Error handling summary

| Failure | Behavior |
|---|---|
| No Last.fm API key | preview route returns `unavailable`; UI shows "preview unavailable", no warn states |
| Last.fm down / upstream error | preview route 500; UI shows "preview unavailable" for that tag |
| tag.getSimilar empty | normal — similar row simply hidden |
| Insights DB missing | vocab from library genres only |
| Navidrome unreachable | vocab from insights DB only |
| Both vocab sources empty | no autocomplete; manual typing + preview still work |

## Testing

- `tests/lastfm/`: `get_tag_preview` happy path, unknown tag (0 taggings), empty similar, partial sub-call failure degrading to empty fields (mocked client).
- `tests/server/test_routes.py`: `/genres/preview` — ok, missing tag 400, no-API-key unavailable, upstream error 500, cache hit (second call doesn't re-invoke client). `/genres/vocab` — merged sources with seeded insights DB, missing-DB fallback, both-empty case.
- Frontend: no JS harness (unchanged project constraint); manual verification in the mix editor.
