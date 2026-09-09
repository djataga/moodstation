"""Orchestration : synchronisation bibliothèque -> index -> playlists DS Audio."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .config import load_config
from .db import DB
from .llm import LLM
from .nlu import suggest_filters
from .playlist import Filters, build_playlist
from .synology_client import SynologyClient, SynologyError
from .tagger import tag_missing


def _norm(text: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return "".join(c for c in plain if c.isalnum())


def make_llm(cfg: Dict[str, Any]) -> LLM:
    return LLM(cfg.get("llm", {}))


def make_db(cfg: Dict[str, Any]) -> DB:
    return DB(cfg["library"]["db_path"])


def make_client(cfg: Dict[str, Any]) -> SynologyClient:
    sc = cfg["synology"]
    return SynologyClient(
        host=sc["host"],
        account=sc["account"],
        password=sc["password"],
        session=sc.get("session", "moodstation"),
    )


# ----------------------------------------------------------------- indexation

def index_library(cfg: Dict[str, Any], db: DB, client: SynologyClient) -> int:
    """Récupère toute la bibliothèque via l'API et l'insère en base locale."""
    count = 0
    batch: List[Dict[str, Any]] = []
    for song in client.list_songs():
        tag = song.get("additional", {}).get("song_tag", {}) or {}
        batch.append(
            {
                "nas_id": song.get("id"),
                "title": song.get("title") or "",
                "artist": song.get("artist") or "",
                "album": song.get("album") or "",
                "genre": tag.get("genre") or "",
                "year": tag.get("year"),
                "duration_s": song.get("duration"),
                "bpm": None,
            }
        )
        count += 1
        if len(batch) >= 500:
            for t in batch:
                db.upsert_track(t)
            batch = []
            print(f"  {count} morceaux indexés…")
    for t in batch:
        db.upsert_track(t)
    return count


def enrich_from_files(cfg: Dict[str, Any], db: DB) -> int:
    """Complète avec les tags ID3 lus directement sur le partage monté (BPM…).

    Facultatif : sans montage local, l'API Audio Station suffit.
    """
    root = cfg["library"].get("local_music_path")
    if not root or not os.path.isdir(root):
        return 0
    try:
        import mutagen
    except ImportError:
        return 0

    rows = db.conn.execute(
        "SELECT id, title, artist, path FROM tracks WHERE path IS NULL OR bpm IS NULL"
    ).fetchall()
    if not rows:
        return 0

    by_key = {}
    for row in rows:
        by_key[_norm(f"{row['artist']}{row['title']}")] = row

    patched = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith((".mp3", ".flac", ".m4a", ".ogg", ".wav")):
                continue
            full = os.path.join(dirpath, name)
            try:
                meta = mutagen.File(full, easy=True)
            except Exception:
                continue
            if meta is None:
                continue
            title = (meta.get("title") or [""])[0]
            artist = (meta.get("artist") or [""])[0]
            key = _norm(f"{artist}{title}")
            row = by_key.get(key)
            if row is None:
                continue
            bpm = None
            try:
                raw = meta.get("bpm") or meta.get("TBPM")
                if raw:
                    bpm = float(str(raw[0]).replace(",", "."))
            except (TypeError, ValueError):
                bpm = None
            db.conn.execute(
                "UPDATE tracks SET path = ?, bpm = COALESCE(?, bpm) WHERE id = ?",
                (full, bpm, row["id"]),
            )
            patched += 1
    db.conn.commit()
    return patched


# ----------------------------------------------------------------- playlists

def push_playlist(
    cfg: Dict[str, Any],
    client: SynologyClient,
    name: str,
    tracks: List[Any],
) -> bool:
    """Crée/remplace une playlist dans DS Audio avec les morceaux sélectionnés."""
    prefix = cfg["playlists"].get("prefix", "Mood")
    full_name = f"{prefix} • {name}" if prefix else name
    ids = [t["nas_id"] for t in tracks if t["nas_id"]]
    if not ids:
        print(f"  ⚠ {full_name} : aucun morceau à pousser, ignorée")
        return False
    try:
        client.replace_playlist(full_name, ids)
        print(f"  ✓ {full_name} ({len(ids)} morceaux) → DS Audio")
        return True
    except SynologyError as err:
        print(f"  ✗ {full_name} : {err}")
        return False


def regenerate_presets(
    cfg: Dict[str, Any], db: DB, client: SynologyClient, llm: LLM
) -> int:
    """Régénère toutes les playlists préréglées de la configuration."""
    pushed = 0
    for preset in cfg["playlists"].get("presets", []):
        name = preset.get("name", "Sans nom")
        size = int(preset.get("size", cfg["playlists"].get("default_size", 40)))
        filters = suggest_filters(preset.get("prompt", ""), llm, default_size=size)
        filters.size = size
        filters.max_per_artist = int(cfg["playlists"].get("max_per_artist", 2))
        candidates = db.candidates(filters)
        tracks = build_playlist(candidates, filters)
        if push_playlist(cfg, client, name, tracks):
            pushed += 1
    return pushed


# ------------------------------------------------------------------- commandes

def full_sync(cfg_path: str = "") -> None:
    cfg = load_config(cfg_path)
    llm = make_llm(cfg)
    db = make_db(cfg)
    print("Connexion au Synology…")
    with make_client(cfg) as client:
        total = index_library(cfg, db, client)
        print(f"Bibliothèque indexée : {total} morceaux")

        patched = enrich_from_files(cfg, db)
        if patched:
            print(f"Tags ID3 complétés depuis le montage local : {patched}")

        if cfg["sync"].get("auto_tag_new", True):
            batch = int(cfg["llm"].get("batch_tracks", 100))
            print("Enrichissement des ambiances…")
            tag_missing(db, llm, batch=batch)

        print("Régénération des playlists préréglées…")
        pushed = regenerate_presets(cfg, db, client, llm)
        print(f"Terminé : {pushed} playlist(s) mise(s) à jour dans DS Audio.")
    db.close()


def generate_now(
    prompt: str,
    name: str = "",
    size: int = 0,
    dry_run: bool = False,
    cfg_path: str = "",
) -> List[Dict[str, Any]]:
    """Génère une playlist à la demande et la pousse dans DS Audio."""
    cfg = load_config(cfg_path)
    default_size = int(cfg["playlists"].get("default_size", 40))
    size = size or default_size
    llm = make_llm(cfg)
    db = make_db(cfg)

    filters = suggest_filters(prompt, llm, default_size=size)
    filters.size = size
    filters.max_per_artist = int(cfg["playlists"].get("max_per_artist", 2))
    desc = ", ".join(sorted(filters.moods)) or "—"
    themes = ", ".join(sorted(filters.themes)) or "—"
    print(f"Filtres retenus — ambiances : {desc} | thèmes : {themes} | "
          f"énergie {filters.energy_min:.1f}–{filters.energy_max:.1f}")

    candidates = db.candidates(filters)
    tracks = build_playlist(candidates, filters)
    if not tracks:
        print("Aucun morceau ne correspond. Lancez d'abord `moodstation sync`.")
        db.close()
        return []

    playlist_name = name or prompt.strip()[:40] or "Personnalisée"
    if dry_run:
        print(f"[dry-run] playlist « {playlist_name} » ({len(tracks)} morceaux) :")
        for i, t in enumerate(tracks, 1):
            print(f"  {i:3}. {t['artist']} — {t['title']}")
    else:
        with make_client(cfg) as client:
            push_playlist(cfg, client, playlist_name, tracks)
    db.close()
    return [dict(t) for t in tracks]


def show_stats(cfg_path: str = "") -> None:
    cfg = load_config(cfg_path)
    db = make_db(cfg)
    counts = db.count()
    print(f"Bibliothèque locale : {counts['total']} morceaux, "
          f"{counts['tagged']} enrichis (dont {counts['by_llm']} par le LLM)")
    moods = db.mood_stats()
    if moods:
        print("Ambiances les plus représentées :")
        for row in moods:
            print(f"  {row['mood']:<12} {row['c']}")
    else:
        print("Aucune ambiance en base — lancez `moodstation sync`.")
    db.close()
