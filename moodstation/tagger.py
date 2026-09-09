"""Enrichissement des morceaux : heuristiques immédiates + LLM en batch.

Stratégie adaptée au Pi 3B : chaque nouveau morceau est d'abord tagué par
heuristiques (instantané, la bibliothèque est utilisable tout de suite),
puis le LLM local affine progressivement par lots nocturnes. Les résultats
sont mis en cache en base : un morceau n'est jamais analysé deux fois.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .db import DB
from .llm import LLM
from .vocab import MOODS, THEMES

# Genre (normalisé) -> (moods, énergie de base 0..1)
_GENRE_HINTS: List[Tuple[str, List[str], float]] = [
    (r"jazz", ["relax", "chill"], 0.35),
    (r"blues", ["melancholic", "relax"], 0.35),
    (r"metal", ["energetic", "aggressive"], 0.9),
    (r"punk|hardcore", ["energetic", "aggressive"], 0.85),
    (r"rock", ["energetic"], 0.7),
    (r"pop", ["happy"], 0.6),
    (r"classi|symphon|opera", ["focus", "relax"], 0.3),
    (r"electro|techno|house|trance|edm", ["energetic", "party"], 0.8),
    (r"ambient|new\s?age", ["focus", "dreamy", "relax"], 0.15),
    (r"hip.?hop|rap", ["energetic", "party"], 0.7),
    (r"reggae|ska", ["chill", "happy"], 0.5),
    (r"folk|acoustic", ["chill", "relax"], 0.3),
    (r"country", ["chill", "melancholic"], 0.4),
    (r"funk|disco", ["party", "happy"], 0.7),
    (r"soul", ["romantic", "melancholic"], 0.4),
    (r"r\.?\s?&?\s?b", ["romantic", "chill"], 0.4),
    (r"soundtrack|bande\s+originale|ost", ["dreamy", "focus"], 0.4),
    (r"latino|salsa|zouk|samba", ["party"], 0.75),
    (r"chanson", ["chill", "melancholic"], 0.4),
    (r"world|ethnique", ["dreamy"], 0.45),
]


def heuristic_tags(track: Any) -> Tuple[List[str], List[str], float]:
    """Tags immédiats à partir du genre, du BPM et de mots-clés du titre."""
    genre = (track["genre"] or "").lower() if "genre" in track.keys() else ""
    title = (track["title"] or "").lower()
    artist = (track["artist"] or "").lower()
    blob = f"{genre} {title} {artist}"

    moods: List[str] = []
    energy = 0.5
    for pattern, hint_moods, hint_energy in _GENRE_HINTS:
        if re.search(pattern, blob):
            moods.extend(hint_moods)
            energy = hint_energy
            break

    bpm = track["bpm"] if "bpm" in track.keys() else None
    if bpm:
        bpm = float(bpm)
        if bpm < 90:
            moods.extend(["relax", "chill"])
            energy = min(energy, 0.35)
        elif bpm > 130:
            moods.extend(["energetic"])
            energy = max(energy, 0.75)

    for word in ("relax", "chill", "party", "dreamy", "focus"):
        if word in blob:
            moods.append(word)

    themes = [t for t in THEMES if t in blob]
    if not moods:
        moods = ["chill"]
    moods = list(dict.fromkeys(moods))
    return moods, themes, energy


_LLM_SYSTEM = (
    "Tu es un expert musical. Pour un morceau, tu renvoies STRICTEMENT du JSON "
    "sur une seule ligne, sans texte autour. "
    f"Moods possibles : {', '.join(MOODS)}. Themes possibles : {', '.join(THEMES)}. "
    'Format exact : {"moods": ["..."], "themes": ["..."], "energy": 0.5} '
    "energy est un nombre entre 0.0 (très calme) et 1.0 (très intense). "
    "Choisis 1 à 3 moods et 0 à 2 themes dans les listes fournies."
)


def llm_tags(llm: LLM, track: Any) -> Optional[Tuple[List[str], List[str], float]]:
    """Tags du LLM local, bornés au vocabulaire contrôlé. None si échec."""
    user = (
        f"Titre : {track['title']}. Artiste : {track['artist']}. "
        f"Album : {track['album']}. Genre : {track['genre']}. "
        f"JSON :"
    )
    data = llm.chat_json(_LLM_SYSTEM, user, max_tokens=110, temperature=0.2)
    if not data:
        return None
    moods = [str(m) for m in data.get("moods", []) if isinstance(m, str)]
    themes = [str(t) for t in data.get("themes", []) if isinstance(t, str)]
    try:
        energy = float(data.get("energy", 0.5))
    except (TypeError, ValueError):
        energy = 0.5
    moods = [m for m in moods if m in MOODS]
    themes = [t for t in themes if t in THEMES]
    if not moods:
        return None
    return moods, themes, energy


def tag_missing(db: DB, llm: Optional[LLM], batch: int = 0, verbose: bool = True) -> Dict[str, int]:
    """Tague les morceaux non enrichis : heuristiques d'abord, LLM ensuite."""
    stats = {"heuristic": 0, "llm": 0, "failed": 0}
    pending = db.untagged()
    if verbose:
        print(f"  {len(pending)} morceau(x) sans ambiance")

    # Passe 1 : heuristiques instantanées
    for row in pending:
        moods, themes, energy = heuristic_tags(row)
        db.set_tags(row["id"], moods, themes, energy, "heuristic")
        stats["heuristic"] += 1
    if verbose and stats["heuristic"]:
        print(f"  heuristiques : {stats['heuristic']} morceau(x) tagué(s)")

    # Passe 2 : le LLM affine les morceaux tagués par heuristique uniquement
    if llm is not None and llm.enabled and batch > 0:
        rows = db.conn.execute(
            "SELECT * FROM tracks WHERE tagged_by = 'heuristic' LIMIT ?",
            (batch,),
        ).fetchall()
        for i, row in enumerate(rows, 1):
            result = llm_tags(llm, row)
            if result is None:
                stats["failed"] += 1
                continue
            moods, themes, energy = result
            db.set_tags(row["id"], moods, themes, energy, "llm")
            stats["llm"] += 1
            if verbose and (i % 10 == 0 or i == len(rows)):
                print(f"  LLM : {i}/{len(rows)} morceau(x) raffiné(s)")
    return stats
