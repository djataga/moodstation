"""Tests du client Synology contre une fausse API (mock, aucun réseau réel)."""

from unittest.mock import MagicMock, patch

import pytest

from moodstation.synology_client import SynologyClient, SynologyError


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    return resp


def test_login_stores_sid():
    client = SynologyClient("http://nas:5000", "user", "pass")
    api_info = {
        "data": {
            "SYNO.API.Auth": {"path": "entry.cgi", "maxVersion": 6},
            "SYNO.AudioStation.Song": {"path": "entry.cgi", "maxVersion": 4},
            "SYNO.AudioStation.Playlist": {"path": "entry.cgi", "maxVersion": 3},
        },
        "success": True,
    }
    login = {"data": {"sid": "abc123"}, "success": True}
    with patch("requests.get", side_effect=[_response(api_info), _response(login)]):
        client.login()
    assert client.sid == "abc123"


def test_error_403_gives_clear_2fa_message():
    client = SynologyClient("http://nas:5000", "user", "pass")
    client._api["SYNO.API.Auth"] = ("entry.cgi", 6)
    auth_fail = {"success": False, "error": {"code": 403}}
    with patch("requests.get", return_value=_response(auth_fail)):
        with pytest.raises(SynologyError) as exc:
            client.login()
    assert "2FA" in str(exc.value)


def test_sid_injection_and_logout():
    client = SynologyClient("http://nas:5000", "user", "pass")
    client.sid = "sess1"
    client._api["SYNO.API.Auth"] = ("entry.cgi", 6)
    ok = {"data": {}, "success": True}
    with patch("requests.get", return_value=_response(ok)) as mock_get:
        client.logout()
    url = mock_get.call_args.args[0]
    params = mock_get.call_args.kwargs["params"]
    assert url == "http://nas:5000/webapi/entry.cgi"
    assert params["_sid"] == "sess1"
    assert params["method"] == "logout"
    assert client.sid is None


def test_replace_playlist_deletes_then_creates():
    client = SynologyClient("http://nas:5000", "user", "pass")
    client.sid = "sess1"
    client._api["SYNO.AudioStation.Playlist"] = ("entry.cgi", 3)
    listing = {
        "data": {"playlists": [{"id": "pl_9", "name": "Mood • Test"}]},
        "success": True,
    }
    ok = {"data": {}, "success": True}
    with patch("requests.get", return_value=_response(ok)) as mock_get:
        # 1er appel : list (find_playlist), puis delete, puis create
        mock_get.side_effect = [_response(listing), _response(ok), _response(ok)]
        client.replace_playlist("Mood • Test", ["music_1", "music_2"])
    methods = [call.kwargs["params"]["method"]
               for call in mock_get.call_args_list]
    assert methods == ["list", "delete", "create"]
    create_params = mock_get.call_args_list[-1].kwargs["params"]
    assert create_params["songs"] == "music_1,music_2"
    assert create_params["name"] == "Mood • Test"


def test_list_songs_paginates():
    client = SynologyClient("http://nas:5000", "user", "pass")
    client.sid = "sess1"
    client._api["SYNO.AudioStation.Song"] = ("entry.cgi", 3)
    page1 = {"data": {"songs": [{"id": f"music_{i}"} for i in range(3)],
                      "total": 4}, "success": True}
    page2 = {"data": {"songs": [{"id": "music_99"}], "total": 4}, "success": True}
    with patch("requests.get", side_effect=[_response(page1), _response(page2)]):
        ids = [s["id"] for s in client.list_songs(page_size=3)]
    assert ids == ["music_0", "music_1", "music_2", "music_99"]
