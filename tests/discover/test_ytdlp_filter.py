import pytest
from discover.ytdlp_adapter import _is_music_result


def _entry(title, uploader=None, channel=None):
    return {"title": title, "uploader": uploader, "channel": channel}


def test_passes_when_artist_in_title():
    assert _is_music_result(_entry("Burial - Archangel", "SomeChannel"), "Burial") is True


def test_passes_when_artist_in_uploader():
    assert _is_music_result(_entry("Archangel Official Audio", "Burial"), "Burial") is True


def test_passes_when_artist_in_channel_field():
    assert _is_music_result(_entry("Archangel (2005)", uploader=None, channel="Burial"), "Burial") is True


def test_fails_when_artist_absent_from_title_and_channel():
    assert _is_music_result(_entry("Morning Coffee Vibes", "CoffeeTV"), "Burial") is False


def test_fails_on_junk_keyword_in_title():
    assert _is_music_result(_entry("Burial - Archangel Guitar Tutorial", "GuitarHub"), "Burial") is False


def test_fails_on_review_keyword():
    assert _is_music_result(_entry("Burial Archangel Review - Best Album?", "MusicCritic"), "Burial") is False


def test_extra_junk_keyword_blocks_result():
    assert _is_music_result(
        _entry("Burial - Live Freestyle", "Burial"),
        "Burial",
        extra_junk=frozenset({"freestyle"}),
    ) is False


def test_case_insensitive_artist_match():
    assert _is_music_result(_entry("BURIAL - ARCHANGEL", "XLRecordings"), "burial") is True


def test_partial_artist_name_does_not_pass():
    # "burie" is not "burial"
    assert _is_music_result(_entry("burie - something", "SomeChannel"), "Burial") is False
