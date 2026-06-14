import pytest


@pytest.fixture
def follows_path(tmp_path):
    return str(tmp_path / "follows.json")


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "follow_state.json")
