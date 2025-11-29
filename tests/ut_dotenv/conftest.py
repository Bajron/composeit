import pytest


@pytest.fixture
def dotenv_path(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"")
    yield path
