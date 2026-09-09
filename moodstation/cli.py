"""Interface en ligne de commande MoodStation."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .config import ConfigError
from .llm import LLM
from .sync import full_sync, generate_now, make_llm, show_stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moodstation",
        description=(
            "Playlists dynamiques par ambiance pour DS Audio : le NAS garde la "
            "bibliothèque, le Raspberry Pi apporte l'IA locale."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config", default="", help="chemin du config.yaml (défaut : ./config.yaml)"
    )
    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser(
        "sync", help="indexe la bibliothèque, enrichit, régénère les playlists"
    )
    p_sync.add_argument(
        "--no-llm", action="store_true",
        help="heuristiques seulement, même si le LLM est configuré",
    )

    p_gen = sub.add_parser(
        "generate", help='crée une playlist à partir d\'une phrase, ex : '
        '"jazz calme pour dîner, 30 titres"'
    )
    p_gen.add_argument("prompt", help="description libre de l'ambiance voulue")
    p_gen.add_argument("--name", default="", help="nom de la playlist dans DS Audio")
    p_gen.add_argument("--size", type=int, default=0, help="nombre de morceaux")
    p_gen.add_argument(
        "--dry-run", action="store_true", help="affiche la sélection sans toucher au NAS"
    )

    sub.add_parser("stats", help="état de l'index local (morceaux, ambiances)")

    p_doc = sub.add_parser("doctor", help="vérifie NAS, base et serveur LLM")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "sync":
            full_sync(args.config)
        elif args.command == "generate":
            generate_now(
                args.prompt,
                name=args.name,
                size=args.size,
                dry_run=args.dry_run,
                cfg_path=args.config,
            )
        elif args.command == "stats":
            show_stats(args.config)
        elif args.command == "doctor":
            return _doctor(args.config)
    except ConfigError as err:
        print(f"Configuration : {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130
    return 0


def _doctor(cfg_path: str) -> int:
    """Diagnostic : config, base locale, serveur LLM, NAS."""
    from .config import load_config
    from .db import DB
    from .sync import make_client

    ok = True
    try:
        cfg = load_config(cfg_path)
        print(f"✓ Configuration chargée ({cfg['synology']['host']})")
    except ConfigError as err:
        print(f"✗ {err}")
        return 2

    try:
        db = DB(cfg["library"]["db_path"])
        counts = db.count()
        print(f"✓ Base SQLite OK ({counts['total']} morceaux, "
              f"{counts['tagged']} enrichis)")
        db.close()
    except Exception as err:  # pragma: no cover - diagnostic interactif
        print(f"✗ Base SQLite : {err}")
        ok = False

    llm = make_llm(cfg)
    if llm.enabled:
        if llm.available():
            print(f"✓ Serveur LLM joignable ({llm.base_url}, modèle {llm.model})")
        else:
            print(f"✗ Serveur LLM injoignable ({llm.base_url}) — "
                  "les heuristiques prendront le relais")
            ok = False
    else:
        print("• LLM désactivé : heuristiques seules")

    try:
        with make_client(cfg) as client:
            total = sum(1 for _ in client.list_songs())
        print(f"✓ Audio Station OK ({total} morceaux accessibles)")
    except Exception as err:
        print(f"✗ Audio Station : {err}")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
