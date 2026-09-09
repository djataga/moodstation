"""Tests du moteur de playlist et de la base locale (sans réseau)."""

from collections import Counter

import pytest

from moodstation.db import DB
from moodstation.playlist import Filters, build_playlist, sequence_tracks


@pytest.fixture
def db(tmp_path):
    db = DB(str(tmp_path / "test.db"))
    artists = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    moods = [["relax", "chill"], ["energetic", "party"], ["focus"], ["happy"], ["dark"]]
    for i in range(50):
        tid = db.upsert_track(
            {
                "nas_id": f"music_{i}",
                "title": f"Titre {i}",
                "artist": artists[i % len(artists)],
                "album": f"Album {i // 5}",
                "genre": ["Jazz", "Rock", "Electro"][i % 3],
                "year": 2000 + (i % 20),
                "duration_s": 200,
                "bpm": None,
            }
        )
        # Cycle de 4 moods pour 5 artistes : chaque ambiance est répartie
        # sur plusieurs artistes (sinon la diversité ne peut pas s'appliquer).
        db.set_tags(tid, moods[i % 4], ["night"], (i % 10) / 10.0, "heuristic")
    yield db
    db.close()


def test_candidates_match_moods(db):
    f = Filters(moods={"relax"}, size=10)
    rows = db.candidates(f)
    assert rows, "des candidats doivent être trouvés"
    assert all(r["score"] >= 2.0 for r in rows), "les morceaux relax doivent scorer"


def test_candidates_energy_window(db):
    f = Filters(moods={"energetic"}, energy_min=0.5, size=20)
    rows = db.candidates(f)
    assert len(rows) >= 5, "seuls les morceaux assez énergiques doivent passer"
    assert all(r["energy"] >= 0.5 for r in rows)


def test_build_playlist_respects_size_and_diversity(db):
    f = Filters(moods={"relax", "chill"}, size=10, max_per_artist=2)
    tracks = build_playlist(db.candidates(f), f)
    assert len(tracks) == 10
    per_artist = Counter((t["artist"] or "").lower() for t in tracks)
    assert max(per_artist.values()) <= 2


def test_sequence_follows_energy_curve(db):
    rows = db.conn.execute("SELECT * FROM tracks ORDER BY id LIMIT 20").fetchall()
    ordered = sequence_tracks(list(rows))
    assert len(ordered) == 20
    first = ordered[0]["energy"] if ordered[0]["energy"] is not None else 0.5
    assert 0.2 <= first <= 0.75, "la playlist doit démarrer sur une énergie moyenne"


def test_filters_normalization():
    f = Filters(
        moods={"relax", "inconnu"},
        themes={"night", "bogus"},
        energy_min=-1,
        energy_max=5,
        size=-3,
        max_per_artist=0,
    ).normalized()
    assert f.moods == {"relax"}
    assert f.themes == {"night"}
    assert f.energy_min == 0.0
    assert f.energy_max == 1.0
    assert f.size == 1
    assert f.max_per_artist == 1
