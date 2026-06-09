def _is_not_owned(artist: dict) -> bool:
    # Navidrome returns id == "-1" (or -1) for similar artists not in the library.
    return str(artist.get("id")) == "-1"


def expand_similar(subsonic, seeds, per_seed: int = 20):
    """Seeds -> scored not-owned similar artists (score = how many seeds suggested them)."""
    seed_names = {s["name"].casefold() for s in seeds}
    scores: dict[str, int] = {}
    for seed in seeds:
        for sim in subsonic.get_artist_info2(seed["id"], count=per_seed):
            name = sim.get("name")
            if not name or not _is_not_owned(sim):
                continue
            if name.casefold() in seed_names:
                continue
            scores[name] = scores.get(name, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    return [{"name": n, "score": s} for n, s in ranked]
