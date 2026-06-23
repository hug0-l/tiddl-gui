import pytest
import json

from pydantic import BaseModel
from pytest_mock import MockerFixture
from pathlib import Path

from tiddl.core.api.client import TidalClient, ApiError


@pytest.mark.asyncio
async def test_tidal_client_init(mocker: MockerFixture):
    client = TidalClient(
        token="test-token",
        cache_name="test_cache",
        omit_cache=True,
        debug_path=Path("/tmp/debug"),
    )

    assert client.token == "test-token"
    assert client.debug_path == Path("/tmp/debug")
    assert client._session is None
    assert client._cache_name == "test_cache"
    assert client._omit_cache is True


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_cache", [True, False])
async def test_omit_cache_flag(mocker: MockerFixture, omit_cache: bool):
    client = TidalClient("token", "cache", omit_cache=omit_cache)
    assert client._omit_cache is omit_cache


class DummyModel(BaseModel):
    foo: str


@pytest.mark.asyncio
async def test_fetch_success(mocker: MockerFixture, tmp_path: Path):
    mock_response = mocker.AsyncMock()
    mock_response.status = 200
    mock_response.from_cache = False
    mock_response.json.return_value = {"foo": "bar"}

    mock_get_cm = mocker.AsyncMock()
    mock_get_cm.__aenter__.return_value = mock_response
    mock_get_cm.__aexit__.return_value = False

    mock_session = mocker.Mock()
    mock_session.get.return_value = mock_get_cm

    mocker.patch("tiddl.core.api.client.API_URL", "https://api.test")
    client = TidalClient("token", str(tmp_path / "cache"), debug_path=tmp_path)
    client._session = mock_session

    result = await client.fetch(DummyModel, "albums/123", {"limit": 10}, expire_after=999)
    assert result.foo == "bar"

    mock_session.get.assert_called_once()
    call_kwargs = mock_session.get.call_args[1]
    assert call_kwargs["params"] == {"limit": 10}
    assert call_kwargs["headers"]["Authorization"] == "Bearer token"

    debug_file = tmp_path / "albums/123.json"
    assert debug_file.exists()

    content = json.loads(debug_file.read_text())
    assert content["status_code"] == 200
    assert content["endpoint"] == "albums/123"
    assert content["params"]["limit"] == 10
    assert content["data"]["foo"] == "bar"


@pytest.mark.asyncio
async def test_fetch_error_raises_api_error(mocker: MockerFixture, tmp_path: Path):
    mock_response = mocker.AsyncMock()
    mock_response.status = 400
    mock_response.from_cache = False
    mock_response.json.return_value = {
        "status": 400,
        "subStatus": "Bad request",
        "userMessage": "user_message",
    }

    mock_get_cm = mocker.AsyncMock()
    mock_get_cm.__aenter__.return_value = mock_response
    mock_get_cm.__aexit__.return_value = False

    mock_session = mocker.Mock()
    mock_session.get.return_value = mock_get_cm

    client = TidalClient("token", str(tmp_path / "cache"))
    client._session = mock_session

    with pytest.raises(ApiError):
        await client.fetch(DummyModel, "bad/endpoint")
