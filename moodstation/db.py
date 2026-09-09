"""Base SQLite locale : catalogue des morceaux + index d'ambiances.

La base vit sur le Raspberry Pi. Elle est reconstruite/complétée à chaque
sync depuis l'API Audio Station, puis enrichie (heuristiques + LLM local).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .playlist import Filters

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    nas_id      TEXT UNIQUE,
    path        TEXT,
    title       TEXT NOT NULL,
    artist      TEXT DEFAULT '',
    album       TEXT DEFAULT '',
    genre       TEXT DEFAULT '',
    year        INTEGER,
    duration_s  INTEGER,
    bpm         REAL,
    energy      REAL,
    tagged_by   TEXT,          -- 'llm' | 'heuristic' | NULL
    tagged_at   TEXT,
    indexed_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre);
CREATE TABLE IF NOT EXISTS track_moods (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    mood     TEXT NOT NULL,
    PRIMARY KEY (track_id, mood)
);
CREATE TABLE IF NOT EXISTS track_themes (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    theme    TEXT NOT NULL,
    PRIMARY KEY (track_id, theme)
);
"""


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- tracks

    def upsert_track(self, t: Dict[str, Any]) -> int:
        """Insère ou met à jour un morceau (clé : nas_id). Retourne l'id interne."""
        cur = self.conn.execute(
            """
            INSERT INTO tracks (nas_id, path, title, artist, album, genre,
                                year, duration_s, bpm)
            VALUES (:nas_id, :path, :title, :artist, :album, :genre,
                    :year, :duration_s, :bpm)
            ON CONFLICT(nas_id) DO UPDATE SET
                path=COALESCE(excluded.path, tracks.path),
                title=excluded.title,
                artist=excluded.artist,
                album=excluded.album,
                genre=excluded.genre,
                year=COALESCE(excluded.year, tracks.year),
                duration_s=COALESCE(excluded.duration_s, tracks.duration_s),
                bpm=COALESCE(excluded.bpm, tracks.bpm)
            """,
            {
                "nas_id": t.get("nas_id"),
                "path": t.get("path"),
                "title": t.get("title") or "",
                "artist": t.get("artist") or "",
                "album": t.get("album") or "",
                "genre": t.get("genre") or "",
                "year": t.get("year"),
                "duration_s": t.get("duration_s"),
                "bpm": t.get("bpm"),
            },
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_path(self, track_id: int, path: str) -> None:
        self.conn.execute(
            "UPDATE tracks SET path = ? WHERE id = ?", (path, track_id)
        )
        self.conn.commit()

    def tracks_without_path(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, title, artist FROM tracks WHERE path IS NULL"
        ).fetchall()

    def untagged(self, limit: int = 0) -> List[sqlite3.Row]:
        sql = "SELECT * FROM tracks WHERE tagged_by IS NULL"
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql).fetchall()

    def set_tags(
        self,
        track_id: int,
        moods: Sequence[str],
        themes: Sequence[str],
        energy: Optional[float],
        tagged_by: str,
    ) -> None:
        self.conn.execute("DELETE FROM track_moods WHERE track_id = ?", (track_id,))
        self.conn.execute("DELETE FROM track_themes WHERE track_id = ?", (track_id,))
        for mood in set(moods):
            self.conn.execute(
                "INSERT OR IGNORE INTO track_moods (track_id, mood) VALUES (?, ?)",
                (track_id, mood),
            )
        for theme in set(themes):
            self.conn.execute(
                "INSERT OR IGNORE INTO track_themes (track_id, theme) VALUES (?, ?)",
                (track_id, theme),
            )
        energy_val = max(0.0, min(1.0, float(energy))) if energy is not None else None
        self.conn.execute(
            "UPDATE tracks SET energy = ?, tagged_by = ?, tagged_at = datetime('now') "
            "WHERE id = ?",
            (energy_val, tagged_by, track_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------ sélection

    def candidates(self, f: Filters, multiplier: int = 6) -> List[sqlite3.Row]:
        """Morceaux scorés selon les filtres d'ambiance (les meilleurs d'abord).

        Les paramètres `?` sont assemblés dans l'ordre du texte SQL :
        d'abord la partie SELECT (scoring), puis la partie WHERE (filtres).
        """
        wheres = ["t.tagged_by IS NOT NULL"]
        score = ["0.0"]
        select_params: List[Any] = []
        where_params: List[Any] = []

        if f.moods:
            ph = ",".join("?" for _ in f.moods)
            score.append(
                f"(SELECT COUNT(*) FROM track_moods tm "
                f"WHERE tm.track_id = t.id AND tm.mood IN ({ph})) * 2.0"
            )
            select_params.extend(f.moods)
            wheres.append(
                f"EXISTS (SELECT 1 FROM track_moods tm "
                f"WHERE tm.track_id = t.id AND tm.mood IN ({ph}))"
            )
            where_params.extend(f.moods)

        if f.themes:
            ph = ",".join("?" for _ in f.themes)
            score.append(
                f"(SELECT COUNT(*) FROM track_themes tt "
                f"WHERE tt.track_id = t.id AND tt.theme IN ({ph})) * 1.0"
            )
            select_params.extend(f.themes)

        if f.genres:
            ph = ",".join("?" for _ in f.genres)
            score.append(f"CASE WHEN lower(t.genre) IN ({ph}) THEN 1.5 ELSE 0.0 END")
            select_params.extend(g.lower() for g in f.genres)
            wheres.append(f"lower(t.genre) IN ({ph})")
            where_params.extend(g.lower() for g in f.genres)

        if f.energy_min is not None:
            wheres.append("t.energy >= ?")
            where_params.append(f.energy_min)
        if f.energy_max is not None:
            wheres.append("t.energy <= ?")
            where_params.append(f.energy_max)

        limit = max(f.size * multiplier, 60)
        sql = (
            f"SELECT t.*, {(' + '.join(score))} AS score "
            f"FROM tracks t WHERE {' AND '.join(wheres)} "
            f"GROUP BY t.id ORDER BY score DESC, RANDOM() LIMIT {int(limit)}"
        )
        return self.conn.execute(sql, select_params + where_params).fetchall()

    def count(self) -> Dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) c FROM tracks").fetchone()["c"]
        tagged = self.conn.execute(
            "SELECT COUNT(*) c FROM tracks WHERE tagged_by IS NOT NULL"
        ).fetchone()["c"]
        llm = self.conn.execute(
            "SELECT COUNT(*) c FROM tracks WHERE tagged_by = 'llm'"
        ).fetchone()["c"]
        return {"total": total, "tagged": tagged, "by_llm": llm}

    def mood_stats(self, limit: int = 15) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT mood, COUNT(*) c FROM track_moods "
            "GROUP BY mood ORDER BY c DESC LIMIT ?",
            (limit,),
        ).fetchall()
