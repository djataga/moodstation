"""Vocabulaire contrôlé + compréhension des demandes (FR) en filtres.

Deux voies complémentaires :
- heuristiques (mots-clés FR/EN) : instantanées, fonctionnent sans LLM ;
- LLM local : compris le prompt libre en filtres structurés.

Le vocabulaire contrôlé est la clé du système : il rend les filtres
fiables en SQL, même avec un très petit modèle qui « hallucine » parfois.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .playlist import Filters
from .vocab import MOODS, THEMES

__all__ = ["MOODS", "THEMES", "Filters", "suggest_filters", "heuristic_filters",
           "llm_filters", "extract_json_dict", "extract_genres", "extract_size"]

GENRE_WORDS = [
    "jazz", "blues", "rock", "metal", "punk", "pop", "classical",
    "classique", "electro", "electronic", "house", "techno", "trance",
    "ambient", "hip-hop", "hip hop", "rap", "reggae", "folk", "country",
    "funk", "soul", "r&b", "rnb", "disco", "latin", "salsa", "world",
    "soundtrack", "chanson",
]

# Mots-clés FR/EN -> moods / themes. Les accents sont normalisés avant.
_KEYWORDS: List[tuple] = [
    (r"detend|relax|calme|chill|repos|douce?r?s|zen|apais", {"moods": ["relax", "chill"]}),
    (r"soir|nuit|evening|late", {"themes": ["night"]}),
    (r"matin|reveil|lever|morning", {"moods": ["uplifting"], "themes": ["morning"]}),
    (r"sport|course|muscl|entrain|workout|running|cardio", {"moods": ["energetic"], "themes": ["work"]}),
    (r"energ|dynam|motiv|intense|puissan", {"moods": ["energetic", "uplifting"]}),
    (r"focus|concentr|travail|etud|code|productiv", {"moods": ["focus"], "themes": ["work"]}),
    (r"fete|party|soiree|danse|dance|celebr|anniversaire", {"moods": ["party"], "themes": ["celebration"]}),
    (r"trist|melo|deprime|chagrin|pluie|rain|gris", {"moods": ["melancholic"], "themes": ["rain"]}),
    (r"nostalg|souvenir|retro|vintage|ann[ée]es\s*8|ann[ée]es\s*9", {"moods": ["melancholic"], "themes": ["nostalgia"]}),
    (r"roman|amour|love|coeur|c[âa]lin", {"moods": ["romantic"], "themes": ["love"]}),
    (r"d[îi]ner|repas|dinner|apero|cocktail", {"themes": ["dinner"], "moods": ["chill"]}),
    (r"route|voiture|voyage|road\s?trip|autoroute|travel", {"themes": ["road", "travel"]}),
    (r"dodo|sommeil|sleep|endorm|berceu?", {"moods": ["relax", "dreamy"], "themes": ["sleep"]}),
    (r"sombre|dark|goth|noir|m[ée]lancolique\s+profond", {"moods": ["dark"]}),
    (r"ete|summer|plage|vacanc|soleil", {"themes": ["summer"], "moods": ["happy"]}),
    (r"hiver|winter|noel|christmas", {"themes": ["winter"]}),
    (r"foret|montagne|mer|nature|campagne", {"themes": ["nature"]}),
    (r"reve|onirique|dreamy|cosmique|espace", {"moods": ["dreamy"]}),
    (r"content|joyeux|happy|bonheur|sourire|feel\s?good", {"moods": ["happy", "uplifting"]}),
    (r"metal|agressif|hardcore|punch", {"moods": ["aggressive", "energetic"]}),
]

_ENERGY_BY_MOOD = {
    "aggressive": 0.9, "energetic": 0.8, "party": 0.8, "uplifting": 0.65,
    "happy": 0.6, "focus": 0.4, "romantic": 0.35, "dreamy": 0.3,
    "melancholic": 0.3, "chill": 0.3, "relax": 0.2, "dark": 0.5,
}


def _strip_accents(text: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def extract_genres(prompt: str) -> List[str]:
    p = _strip_accents(prompt)
    found = []
    for g in GENRE_WORDS:
        if _strip_accents(g) in p:
            found.append(g)
    return found


def extract_size(prompt: str, default: int = 40) -> int:
    m = re.search(r"(\d{1,3})\s*(?:titres?|morceaux?|tracks?|sons?|chansons?)", prompt, re.I)
    if m:
        n = int(m.group(1))
        return max(5, min(300, n))
    return default


def heuristic_filters(prompt: str, default_size: int = 40) -> Filters:
    """Traduit un prompt libre en filtres via mots-clés (sans réseau, sans LLM)."""
    p = _strip_accents(prompt)
    f = Filters(size=extract_size(prompt, default_size))
    for pattern, targets in _KEYWORDS:
        if re.search(pattern, p):
            f.moods.update(targets.get("moods", []))
            f.themes.update(targets.get("themes", []))
    f.genres.update(extract_genres(prompt))

    if not f.moods and not f.themes:
        # Rien de reconnu : playlist variée mais pas désordonnée
        f.moods.update(["chill", "happy"])
    # Cadre l'énergie autour de l'ambiance dominante
    energies = [_ENERGY_BY_MOOD.get(m, 0.5) for m in f.moods] or [0.5]
    center = sum(energies) / len(energies)
    f.energy_min = max(0.0, center - 0.45)
    f.energy_max = min(1.0, center + 0.45)
    return f


def llm_filters(llm: Any, prompt: str, default_size: int = 40) -> Optional[Filters]:
    """Demande au LLM local de traduire le prompt en filtres. None si échec."""
    system = (
        "Tu traduis une demande musicale en JSON strict, sans aucun texte autour. "
        f"Moods possibles : {', '.join(MOODS)}. Themes possibles : {', '.join(THEMES)}. "
        'Format : {"moods": [...], "themes": [...], "genres": [...], '
        '"energy_min": 0.0, "energy_max": 1.0} '
        "Choisis uniquement dans les listes fournies. Réponds en une seule ligne."
    )
    data = llm.chat_json(system, f"Demande : {prompt}", max_tokens=120)
    if not data:
        return None
    f = Filters(size=extract_size(prompt, default_size))
    f.moods = set(str(m) for m in data.get("moods", []) if isinstance(m, str))
    f.themes = set(str(t) for t in data.get("themes", []) if isinstance(t, str))
    f.genres = set(str(g) for g in data.get("genres", []) if isinstance(g, str))
    try:
        f.energy_min = float(data.get("energy_min", 0.0))
        f.energy_max = float(data.get("energy_max", 1.0))
    except (TypeError, ValueError):
        pass
    return f.normalized()


def suggest_filters(prompt: str, llm: Any = None, default_size: int = 40) -> Filters:
    """LLM d'abord, repli silencieux sur les heuristiques."""
    if llm is not None:
        f = llm_filters(llm, prompt, default_size)
        if f is not None:
            return f
    return heuristic_filters(prompt, default_size)


def extract_json_dict(text: str) -> Optional[Dict[str, Any]]:
    """Extrait le premier objet JSON équilibré d'un texte bruité (petits LLM)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None
