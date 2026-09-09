"""Chargement et validation de la configuration YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_FILE = "config.yaml"


class ConfigError(RuntimeError):
    """Configuration absente ou invalide."""


def config_path(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("MOODSTATION_CONFIG")
    if env:
        return Path(env)
    return Path.cwd() / CONFIG_FILE


def load_config(explicit: str = "") -> Dict[str, Any]:
    path = config_path(explicit)
    if not path.is_file():
        raise ConfigError(
            f"Fichier de configuration introuvable : {path}\n"
            "Copiez l'exemple puis adaptez-le :  cp config.example.yaml config.yaml"
        )
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    _require(cfg, ["synology", "host"], path)
    _require(cfg, ["synology", "account"], path)
    _require(cfg, ["synology", "password"], path)

    # Valeurs par défaut pragmatiques
    cfg.setdefault("library", {})
    cfg["library"].setdefault("local_music_path", "")
    cfg["library"].setdefault("db_path", "data/moodstation.db")

    cfg.setdefault("llm", {})
    cfg["llm"].setdefault("backend", "llamacpp")
    cfg["llm"].setdefault("base_url", "http://127.0.0.1:8081")
    cfg["llm"].setdefault("model", "qwen2.5-0.5b-instruct-q4_k_m")
    cfg["llm"].setdefault("timeout_s", 180)
    cfg["llm"].setdefault("batch_tracks", 100)

    cfg.setdefault("playlists", {})
    cfg["playlists"].setdefault("default_size", 40)
    cfg["playlists"].setdefault("prefix", "Mood")
    cfg["playlists"].setdefault("max_per_artist", 2)
    cfg["playlists"].setdefault("presets", [])

    cfg.setdefault("sync", {})
    cfg["sync"].setdefault("auto_tag_new", True)
    return cfg


def _require(cfg: Dict[str, Any], keys: list, path: Path) -> None:
    node: Any = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            dotted = ".".join(keys)
            raise ConfigError(f"Clé manquante dans {path} : {dotted}")
        node = node[key]
