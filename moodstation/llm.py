"""Client LLM minimal, compatible llama.cpp (`llama-server`) et OpenAI.

Sur Pi 3B on utilise llama.cpp en serveur local avec un très petit modèle
(0.5B en Q4). Le client est volontairement tolérant aux pannes : toute
erreur renvoie None et l'appelant retombe sur les heuristiques.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from .nlu import extract_json_dict


class LLM:
    def __init__(self, cfg: Dict[str, Any]):
        self.backend = (cfg.get("backend") or "none").lower()
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.model = cfg.get("model", "")
        self.timeout = int(cfg.get("timeout_s", 180))
        if self.backend == "none":
            self.enabled = False
        else:
            self.enabled = True

    # ---------------------------------------------------------------- public

    def chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 180,
        temperature: float = 0.2,
        retries: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Une requête -> dict JSON. None si le LLM est indisponible ou confus."""
        if not self.enabled:
            return None
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                data = extract_json_dict(content)
                if data is not None:
                    return data
                # Petit modèle qui bavarde hors JSON : on retente une fois
            except (requests.RequestException, KeyError, ValueError):
                if attempt >= retries:
                    return None
            time.sleep(0.5)
        return None

    def available(self) -> bool:
        """Ping rapide du serveur (utile pour `moodstation doctor`)."""
        if not self.enabled:
            return False
        try:
            resp = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False
