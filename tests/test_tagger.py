"""Tests des heuristiques de tagging et du vocabulaire contrôlé."""

from moodstation.db import DB
from moodstation.nlu import (
    MOODS,
    THEMES,
    extract_json_dict,
    heuristic_filters,
)
from moodstation.tagger import heuristic_tags


def test_genre_jazz_gives_relax_mood(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    tid = db.upsert_track(
        {"nas_id": "music_1", "title": "Smooth Night", "artist": "Quartet",
         "album": "Café", "genre": "Jazz", "year": 1998, "duration_s": 210}
    )
    db.set_tags(tid, ["relax"], [], 0.3, "heuristic")
    row = db.conn.execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone()
    moods, themes, energy = heuristic_tags(row)
    assert "relax" in moods or "chill" in moods
    assert 0 <= energy <= 1
    db.close()


def test_bpm_influences_energy(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    tid = db.upsert_track(
        {"nas_id": "music_2", "title": "Run", "artist": "X", "album": "",
         "genre": "Techno", "year": 2015, "duration_s": 300, "bpm": 140}
    )
    db.set_tags(tid, ["energetic"], [], 0.8, "heuristic")
    row = db.conn.execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone()
    moods, themes, energy = heuristic_tags(row)
    assert energy >= 0.75
    assert "energetic" in moods
    db.close()


def test_heuristic_tags_stay_in_vocabulary(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    tid = db.upsert_track(
        {"nas_id": "music_3", "title": "Night Sleep", "artist": "Y", "album": "",
         "genre": "Ambient", "year": 2020, "duration_s": 400, "bpm": 60}
    )
    db.set_tags(tid, ["relax"], [], 0.2, "heuristic")
    row = db.conn.execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone()
    moods, themes, _ = heuristic_tags(row)
    assert all(m in MOODS for m in moods), "les moods heuristiques restent dans MOODS"
    db.close()


def test_heuristic_filters_french_prompt():
    f = heuristic_filters("musique calme pour le soir, 25 titres", default_size=40)
    assert "relax" in f.moods or "chill" in f.moods
    assert "night" in f.themes
    assert f.size == 25


def test_heuristic_filters_genre_detection():
    f = heuristic_filters("du jazz pour dîner", default_size=30)
    assert "jazz" in f.genres


def test_heuristic_filters_fallback():
    f = heuristic_filters("xyzzy sans mots connus", default_size=15)
    assert f.moods, "un repli doit toujours donner au moins une ambiance"
    assert f.size == 15


def test_vocabulary_consistency():
    assert len(MOODS) >= 8 and len(THEMES) >= 8
    assert len(set(MOODS)) == len(MOODS)
    assert len(set(THEMES)) == len(THEMES)


def test_extract_json_dict_from_noisy_llm_output():
    text = 'Voilà ! {"moods": ["relax"], "themes": ["night"], "energy": 0.2} voilà'
    data = extract_json_dict(text)
    assert data == {"moods": ["relax"], "themes": ["night"], "energy": 0.2}


def test_extract_json_dict_nested_and_none():
    nested = 'blah {"a": {"b": "c}d"}, "e": 1} end'
    assert extract_json_dict(nested) == {"a": {"b": "c}d"}, "e": 1}
    assert extract_json_dict("pas de json ici") is None
