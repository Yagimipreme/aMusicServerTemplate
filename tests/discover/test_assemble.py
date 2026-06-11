import os
from discover.assemble import write_weekly_mix


def test_write_weekly_mix_creates_m3u_with_basenames(tmp_path):
    song_dir = str(tmp_path)
    paths = [os.path.join(song_dir, "a.mp3"), os.path.join(song_dir, "b.mp3")]
    m3u = write_weekly_mix(song_dir, paths, name="Weekly Mix")

    assert os.path.basename(m3u) == "Weekly Mix.m3u"
    content = open(m3u, encoding="utf-8").read().splitlines()
    assert content[0] == "#EXTM3U"
    assert "a.mp3" in content
    assert "b.mp3" in content


def test_write_weekly_mix_appends_to_existing(tmp_path):
    song_dir = str(tmp_path)
    write_weekly_mix(song_dir, [os.path.join(song_dir, "old.mp3")], name="Weekly Mix")
    m3u = write_weekly_mix(song_dir, [os.path.join(song_dir, "new.mp3")], name="Weekly Mix")
    content = open(m3u, encoding="utf-8").read()
    assert "old.mp3" in content
    assert "new.mp3" in content


def test_write_weekly_mix_drops_oldest_when_over_cap(tmp_path):
    song_dir = str(tmp_path)
    old_paths = [os.path.join(song_dir, f"old_{i}.mp3") for i in range(3)]
    write_weekly_mix(song_dir, old_paths, name="Weekly Mix", cap=3)
    m3u = write_weekly_mix(song_dir, [os.path.join(song_dir, "new.mp3")], name="Weekly Mix", cap=3)
    tracks = [l for l in open(m3u).read().splitlines() if l and not l.startswith("#")]
    assert len(tracks) == 3
    assert "old_0.mp3" not in tracks   # oldest dropped
    assert "old_1.mp3" in tracks
    assert "old_2.mp3" in tracks
    assert "new.mp3" in tracks


def test_write_weekly_mix_deduplicates(tmp_path):
    song_dir = str(tmp_path)
    path = os.path.join(song_dir, "a.mp3")
    write_weekly_mix(song_dir, [path], name="Weekly Mix")
    m3u = write_weekly_mix(song_dir, [path], name="Weekly Mix")
    tracks = [l for l in open(m3u).read().splitlines() if l and not l.startswith("#")]
    assert tracks.count("a.mp3") == 1


def test_write_weekly_mix_sanitizes_name(tmp_path):
    m3u = write_weekly_mix(str(tmp_path), [], name="My/Bad:Name")
    assert os.path.basename(m3u) == "My_Bad_Name.m3u"
