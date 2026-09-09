"""Moteur de playlists : sélection diversifiée + séquençage en courbe d'énergie.

Le scoring SQL (db.candidates) ramène un vivier de candidats ; ce module
applique ensuite une diversité par artiste, puis ordonne les morceaux pour
suivre une courbe d'énergie naturelle (départ posé → pic → retombée).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set

from .vocab import MOODS, THEMES


@dataclass
class Filters:
    moods: Set[str] = field(default_factory=set)
    themes: Set[str] = field(default_factory=set)
    genres: Set[str] = field(default_factory=set)
    energy_min: float = 0.0
    energy_max: float = 1.0
    size: int = 40
    max_per_artist: int = 2

    def normalized(self) -> "Filters":
        """Ne garde que des valeurs connues du vocabulaire contrôlé."""
        self.moods = {m for m in self.moods if m in MOODS}
        self.themes = {t for t in self.themes if t in THEMES}
        self.energy_min = max(0.0, min(1.0, self.energy_min))
        self.energy_max = max(self.energy_min, min(1.0, self.energy_max))
        self.size = max(1, int(self.size))
        self.max_per_artist = max(1, int(self.max_per_artist))
        return self

    def is_empty(self) -> bool:
        return not (self.moods or self.themes or self.genres)


def _artist_of(row: Any) -> str:
    return (row["artist"] or "").strip().lower()


def select_diverse(
    candidates: Sequence[Any], f: Filters, rng: random.Random
) -> List[Any]:
    """Choisit `size` morceaux en respectant un quota par artiste.

    Parcourt les candidats par score décroissant ; si le vivier est trop
    pauvre pour tenir le quota, on le relâche pour compléter la playlist.
    """
    if not candidates:
        return []
    rows = list(candidates)
    rng.shuffle(rows)
    rows.sort(key=lambda r: r["score"], reverse=True)

    def pick(cap: int) -> List[Any]:
        chosen: List[Any] = []
        per_artist: Dict[str, int] = {}
        for row in rows:
            if len(chosen) >= f.size:
                break
            artist = _artist_of(row)
            if per_artist.get(artist, 0) >= cap:
                continue
            chosen.append(row)
            per_artist[artist] = per_artist.get(artist, 0) + 1
        return chosen

    chosen = pick(f.max_per_artist)
    if len(chosen) < f.size:
        seen = {r["id"] for r in chosen}
        for row in rows:  # relâche le quota si nécessaire
            if len(chosen) >= f.size:
                break
            if row["id"] not in seen:
                chosen.append(row)
                seen.add(row["id"])
    return chosen


def _target_energy(position: int, total: int) -> float:
    """Courbe cible : ~0.45 au départ, pic ~0.75 au tiers, ~0.5 à la fin."""
    if total <= 1:
        return 0.55
    t = position / (total - 1)
    return 0.45 + 0.30 * (4 * t * (1 - t))  # parabole inversée, pic à t=0.5


def sequence_tracks(tracks: List[Any]) -> List[Any]:
    """Ordonne par proximité d'énergie avec la courbe cible (greedy)."""
    remaining = list(tracks)
    ordered: List[Any] = []
    pos = 0
    while remaining:
        target = _target_energy(pos, len(tracks))
        best = min(
            remaining,
            key=lambda r: abs((r["energy"] if r["energy"] is not None else 0.5) - target)
            + random.random() * 0.05,
        )
        remaining.remove(best)
        ordered.append(best)
        pos += 1
    return ordered


def build_playlist(candidates: Sequence[Any], f: Filters, seed: int = 0) -> List[Any]:
    """Pipeline complet : diversité puis séquençage. Retourne `size` morceaux."""
    f = f.normalized()
    rng = random.Random(seed)
    chosen = select_diverse(candidates, f, rng)
    return sequence_tracks(chosen)
