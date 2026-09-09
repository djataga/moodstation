"""Vocabulaire contrôlé partagé : le contrat entre LLM, heuristiques et SQL.

Le LLM et les heuristiques doivent choisir DANS ces listes ; le moteur SQL
ne filtre que sur ces valeurs. C'est ce qui rend les petits modèles fiables.
"""

MOODS = [
    "relax", "chill", "energetic", "party", "focus", "melancholic",
    "happy", "romantic", "dark", "uplifting", "dreamy", "aggressive",
]
THEMES = [
    "night", "morning", "rain", "summer", "winter", "road", "love",
    "celebration", "work", "sleep", "dinner", "travel", "nostalgia",
    "nature", "city",
]
