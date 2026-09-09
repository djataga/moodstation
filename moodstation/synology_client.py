"""Client de l'API Web Synology (DSM 6.x / Audio Station) — pour le DS213j.

MoodStation n'installe RIEN sur le NAS : il pilote Audio Station à distance
via son API Web officielle (SYNO.API.Auth, SYNO.AudioStation.Song,
SYNO.AudioStation.Playlist). Les playlists créées apparaissent nativement
dans DS Audio (mobile, web, desktop).

NB : ce client a été écrit contre la documentation des API DSM 6 ; les
versions exactes sont découvertes au démarrage via SYNO.API.Info, et les
erreurs sont affichées en clair pour faciliter le diagnostic sur votre NAS.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

# Codes d'erreur courants de l'API Synology (communs + spécifiques DSM)
_ERROR_MESSAGES = {
    100: "Paramètre inconnu",
    101: "API invalide",
    102: "Paramètre manquant dans la requête",
    103: "Méthode non supportée par cette API",
    104: "Version d'API non supportée",
    105: "Permissions insuffisantes (vérifiez les droits de l'utilisateur)",
    106: "Session expirée (timeout)",
    107: "Session interrompue (double connexion ?)",
    109: "Trop de requêtes (rate limit)",
    110: "OTP/2FA requis (utilisez un compte sans 2FA)",
    119: "SID invalide ou expiré",
    400: "Identifiants incorrects",
    401: "Compte désactivé",
    402: "Permission refusée",
    403: "Code 2FA/OTP requis — créez un compte DSM dédié sans 2FA",
    404: "Mot de passe expiré",
    407: "IP bloquée après trop d'échecs — patientez ou redémarrez",
}


class SynologyError(RuntimeError):
    def __init__(self, code: int, context: str = ""):
        msg = _ERROR_MESSAGES.get(code, f"Erreur Synology {code}")
        if context:
            msg = f"{msg} (contexte : {context})"
        super().__init__(msg)
        self.code = code


class SynologyClient:
    def __init__(
        self,
        host: str,
        account: str,
        password: str,
        session: str = "moodstation",
        timeout: int = 30,
    ):
        self.host = host.rstrip("/")
        self.account = account
        self.password = password
        self.session = session
        self.timeout = timeout
        self.sid: Optional[str] = None
        self._api: Dict[str, Tuple[str, int]] = {}  # api -> (chemin cgi, version max)

    # --------------------------------------------------------------- basse couche

    def discover(self) -> None:
        """Interroge SYNO.API.Info pour connaître chemins et versions réels."""
        data = None
        for cgi in ("entry.cgi", "query.cgi"):
            try:
                resp = requests.get(
                    f"{self.host}/webapi/{cgi}",
                    params={
                        "api": "SYNO.API.Info",
                        "version": 1,
                        "method": "query",
                        "query": "SYNO.API.Auth,SYNO.AudioStation",
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("success"):
                    data = payload["data"]
                    break
            except (requests.RequestException, ValueError):
                continue
        if not data:
            raise SynologyError(
                -1,
                "SYNO.API.Info injoignable — vérifiez l'URL du DSM "
                f"({self.host}) et que « Activer le service Web API » est coché",
            )
        for name, info in data.items():
            if isinstance(info, dict) and "path" in info:
                self._api[name] = (info["path"], int(info.get("maxVersion", 1)))

    def _request(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
    ) -> Dict[str, Any]:
        if api not in self._api:
            self.discover()
        path, max_version = self._api.get(api, ("entry.cgi", 1))
        params: Dict[str, Any] = {
            "api": api,
            "version": min(version, max_version),
            "method": method,
        }
        if self.sid:
            params["_sid"] = self.sid
        if extra:
            params.update(extra)
        resp = requests.get(
            f"{self.host}/webapi/{path}", params=params, timeout=self.timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success"):
            code = payload.get("error", {}).get("code", -1)
            if code == 119 and retry_auth and self.sid:
                self.login()  # session expirée : on se reconnecte une fois
                return self._request(api, method, version, extra, retry_auth=False)
            raise SynologyError(code, f"{api}.{method}")
        return payload.get("data") or {}

    # ---------------------------------------------------------------- sessions

    def login(self) -> None:
        data = self._request(
            "SYNO.API.Auth",
            "login",
            version=2,
            extra={
                "account": self.account,
                "passwd": self.password,
                "session": self.session,
                "format": "sid",
            },
            retry_auth=False,
        )
        self.sid = data.get("sid")

    def logout(self) -> None:
        if self.sid:
            try:
                self._request(
                    "SYNO.API.Auth", "logout", version=2,
                    extra={"session": self.session}, retry_auth=False,
                )
            except (SynologyError, requests.RequestException):
                pass
            self.sid = None

    def __enter__(self) -> "SynologyClient":
        self.login()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.logout()

    # ------------------------------------------------------------------ musique

    def list_songs(self, page_size: int = 500) -> Generator[Dict[str, Any], None, None]:
        """Itère sur tous les morceaux de la bibliothèque Audio Station."""
        offset = 0
        while True:
            data = self._request(
                "SYNO.AudioStation.Song",
                "list",
                version=3,
                extra={
                    "offset": offset,
                    "limit": page_size,
                    "additional": "song_tag",
                },
            )
            songs = data.get("songs", [])
            for song in songs:
                yield song
            offset += len(songs)
            if len(songs) < page_size or not songs:
                break

    # --------------------------------------------------------------- playlists

    def list_playlists(self) -> List[Dict[str, Any]]:
        data = self._request(
            "SYNO.AudioStation.Playlist",
            "list",
            version=2,
            extra={"library": "personal", "additional": "songs"},
        )
        return data.get("playlists", [])

    def find_playlist(self, name: str) -> Optional[Dict[str, Any]]:
        for pl in self.list_playlists():
            if pl.get("name") == name:
                return pl
        return None

    def create_playlist(self, name: str, song_ids: List[str]) -> Dict[str, Any]:
        return self._request(
            "SYNO.AudioStation.Playlist",
            "create",
            version=1,
            extra={"name": name, "songs": ",".join(song_ids)},
        )

    def delete_playlist(self, playlist_id: str) -> Dict[str, Any]:
        return self._request(
            "SYNO.AudioStation.Playlist",
            "delete",
            version=1,
            extra={"id": playlist_id},
        )

    def replace_playlist(self, name: str, song_ids: List[str]) -> str:
        """Recrée une playlist du même nom avec les morceaux donnés.

        On passe par suppression + recréation : c'est la voie la plus
        compatible avec les différentes versions de l'API Audio Station.
        (La playlist remonte en haut de la liste dans DS Audio.)
        """
        existing = self.find_playlist(name)
        if existing:
            self.delete_playlist(existing["id"])
            time.sleep(0.3)
        self.create_playlist(name, song_ids)
        return name
