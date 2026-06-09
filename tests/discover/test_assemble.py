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


def test_write_weekly_mix_overwrites_previous(tmp_path):
    song_dir = str(tmp_path)
    write_weekly_mix(song_dir, [os.path.join(song_dir, "old.mp3")], name="Weekly Mix")
    m3u = write_weekly_mix(song_dir, [os.path.join(song_dir, "new.mp3")], name="Weekly Mix")
    content = open(m3u, encoding="utf-8").read()
    assert "old.mp3" not in content
    assert "new.mp3" in content


def test_write_weekly_mix_sanitizes_name(tmp_path):
    m3u = write_weekly_mix(str(tmp_path), [], name="My/Bad:Name")
    assert os.path.basename(m3u) == "My_Bad_Name.m3u"
