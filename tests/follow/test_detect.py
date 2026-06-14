from follow import detect
from follow import fstate


class FakeMB:
    def __init__(self, release_groups=None, tracks=None):
        self._rgs = release_groups or {}      # mbid -> list[rg dict]
        self._tracks = tracks or {}           # rg_mbid -> list[title]
        self.rg_calls = []

    def get_release_groups(self, mbid, limit=100):
        self.rg_calls.append(mbid)
        return self._rgs.get(mbid, [])

    def get_release_tracks(self, rg_mbid):
        return self._tracks.get(rg_mbid, [])


def _follow(mbid, name):
    return {"mbid": mbid, "name": name, "disambiguation": "", "followed_at": ""}


def test_single_from_feed_yields_all_tracks(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-1": ["Lead", "B-side"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-1", "release_name": "Lead",
              "primary_type": "Single", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["title"] for t in targets] == ["Lead", "B-side"]
    assert all(t["rg_mbid"] == "rg-1" for t in targets)


def test_album_from_feed_yields_one_representative(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-2": ["Intro", "Mezzanine", "Outro"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-2", "release_name": "Mezzanine",
              "primary_type": "Album", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["title"] for t in targets] == ["Mezzanine"]   # title-track match


def test_album_without_title_match_uses_first_track(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-3": ["First", "Second"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-3", "release_name": "Some Album",
              "primary_type": "Album", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["title"] for t in targets] == ["First"]


def test_skips_unfollowed_artist(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(tracks={"rg-1": ["X"]})
    fresh = [{"artist_mbids": ["OTHER"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-1", "release_name": "X",
              "primary_type": "Single", "artist_name": "Z"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert targets == []


def test_skips_already_acquired(state_path):
    st = fstate.load(state_path)
    st.mark_acquired("rg-1")
    mb = FakeMB(tracks={"rg-1": ["X"]})
    fresh = [{"artist_mbids": ["m1"], "release_date": "2026-06-12",
              "release_group_mbid": "rg-1", "release_name": "X",
              "primary_type": "Single", "artist_name": "A"}]
    targets = detect.detect_targets(mb, fresh, [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert targets == []


def test_backfill_within_window_marks_backfilled(state_path):
    st = fstate.load(state_path)
    mb = FakeMB(
        release_groups={"m1": [
            {"rg_mbid": "rg-old", "title": "Old", "first_release_date": "2000-01-01",
             "primary_type": "Album"},
            {"rg_mbid": "rg-new", "title": "Recent", "first_release_date": "2026-05-20",
             "primary_type": "Single"},
        ]},
        tracks={"rg-new": ["Recent"]},
    )
    targets = detect.detect_targets(mb, [], [_follow("m1", "A")], st,
                                    default_backfill_days=30, today="2026-06-14")
    assert [t["rg_mbid"] for t in targets] == ["rg-new"]
    assert st.is_backfilled("m1") is True


def test_backfill_runs_once(state_path):
    st = fstate.load(state_path)
    st.mark_backfilled("m1")
    mb = FakeMB(release_groups={"m1": [
        {"rg_mbid": "rg-new", "title": "Recent", "first_release_date": "2026-05-20",
         "primary_type": "Single"}]}, tracks={"rg-new": ["Recent"]})
    detect.detect_targets(mb, [], [_follow("m1", "A")], st,
                          default_backfill_days=30, today="2026-06-14")
    assert mb.rg_calls == []   # backfill skipped for already-backfilled artist
