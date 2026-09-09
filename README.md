# MoodStation 🎵

**Playlists dynamiques par ambiance pour Synology DS Audio, avec IA locale sur Raspberry Pi.**

MoodStation ajoute à DS Audio ce qu'il n'a pas : des playlists générées selon
*l'ambiance* et le *thème* (« calme pour ce soir », « énergie pour le sport,
30 titres », « jazz pour dîner »). Il tourne sur un Raspberry Pi 3B à côté du
NAS et pousse son résultat directement dans DS Audio — rien à installer sur le
Synology, aucune modification de DS Audio : les playlists apparaissent
nativement dans l'application mobile, web et desktop.

```
┌──────────────────────────┐              ┌────────────────────────────────┐
│  DS213j (DSM 6.2)        │              │  Raspberry Pi 3B               │
│                          │   API Web    │                                │
│  • Audio Station (DS     │◄────────────►│  • moodstation (Python)        │
│    Audio côté client)    │  Synology    │    - index SQLite des ambiances│
│  • Fichiers musicaux     │              │    - moteur de playlists       │
│    (partage NFS/SMB      │──montage ───►│    - client API Synology       │
│    en lecture seule)     │  lecture     │  • llama.cpp + petit modèle LLM│
└──────────────────────────┘  seule       │    (enrichissement local)      │
          ▲                               └────────────────────────────────┘
          └── DS Audio (téléphone/web) voit les playlists « Mood • … »
```

## Le principe en trois phases

1. **Indexation** — MoodStation liste votre bibliothèque via l'API Web d'Audio
   Station et lit en plus les tags ID3 (BPM…) sur le partage musical monté en
   lecture seule. Tout est stocké dans une base SQLite locale au Pi.
2. **Enrichissement (ambiance/thème/énergie)** — chaque morceau reçoit des tags
   dans un **vocabulaire contrôlé** :
   - *ambiances* : `relax, chill, energetic, party, focus, melancholic, happy, romantic, dark, uplifting, dreamy, aggressive`
   - *thèmes* : `night, morning, rain, summer, winter, road, love, celebration, work, sleep, dinner, travel, nostalgia, nature, city`
   - une *énergie* de 0.0 (très calme) à 1.0 (très intense).

   Deux passes : des **heuristiques instantanées** (genre, BPM, mots-clés) qui
   rendent la bibliothèque utilisable dès la première sync, puis le **LLM local**
   (llama.cpp, modèle 0.5B quantifié) qui affine par petits lots — idéalement
   chaque nuit, car les résultats sont mis en cache : un morceau n'est jamais
   analysé deux fois.
3. **Génération + push** — une phrase libre est traduite en filtres (par le LLM,
   ou par mots-clés français en repli), le moteur SQL sélectionne des morceaux
   variés (quota par artiste), les ordonne en **courbe d'énergie** (départ posé →
   pic → retombée), puis crée la playlist dans DS Audio via l'API Synology.

Des **playlists préréglées** (configurables) sont régénérées à chaque sync
nocturne : « Mood • Détente », « Mood • Énergie », « Mood • Focus »…

## Pourquoi cette architecture ?

Votre matériel impose des choix, et ce projet est dessiné autour d'eux :

| Machine | Limite | Conséquence sur MoodStation |
|---|---|---|
| DS213j (512 Mo RAM, ARMv7, DSM 6.2 max) | trop juste pour de l'IA | **rien à installer** : MoodStation pilote le NAS à distance via l'API Web |
| Raspberry Pi 3B (1 Go RAM, 4×A53) | LLM très lent (~2–4 tokens/s) | petits modèles (0.5B Q4) réservés au **traitement par lots nocturne**, jamais dans le chemin critique |

Le point clé : **le LLM n'a pas besoin de répondre en temps réel**. Le taggage
est un travail de fond mis en cache ; générer une playlist = de simples requêtes
SQL, instantanées même sur Pi 3B. Si le LLM est éteint ou surchargé, tout
fonctionne quand même (heuristiques).

## Installation

### 1. Sur le DS213j (5 minutes)

1. DSM → **Panneau de configuration → Terminal & SNMP** : rien à activer ici.
2. Vérifiez qu'**Audio Station** est installé (il fournit l'API Web).
3. Panneau de configuration → **Réseau → Services DSM** : « Activer le service
   Web API » doit être coché (c'est le cas par défaut).
4. Créez ou utilisez un compte DSM :
   - **le plus simple** : utilisez *votre* compte principal dans la config —
     les playlists créées sont vos playlists personnelles, visibles
     immédiatement dans DS Audio ;
   - si votre compte a la double authentification (2FA), créez plutôt un
     compte dédié **sans 2FA** avec uniquement le droit Audio Station.
5. *(Optionnel, recommandé)* Montez le dossier musical sur le Pi en lecture
   seule pour lire les tags ID3 (BPM) :

```bash
# Sur le Pi — exemple SMB (adaptez IP et nom de partage)
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/music
echo '//192.168.1.50/music /mnt/music cifs ro, guest,uid=1000 0 0' | sudo tee -a /etc/fstab
sudo mount -a
```

### 2. Sur le Raspberry Pi 3B

Système recommandé : **Raspberry Pi OS Lite 64 bits** (Bookworm), à jour.

```bash
git clone https://github.com/djataga/moodstation.git
cd moodstation
sudo bash install/install_pi.sh            # dépendances + venv + service
```

Le script installe Python, le paquet `moodstation`, et vous guide pour la suite.
Pour tout faire à la main :

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.yaml config.yaml    # puis éditez : host, compte, mot de passe
.venv/bin/moodstation doctor          # diagnostic NAS / base / LLM
.venv/bin/moodstation sync            # première indexation + playlists
```

### 3. Le LLM local (optionnel mais recommandé)

Sur Pi 3B, utilisez **llama.cpp** avec un tout petit modèle quantifié :

```bash
sudo apt install -y build-essential cmake git
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build && cmake --build build -j4 --target llama-server

mkdir -p ~/models && cd ~/models
wget https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf
```

Modèles adaptés au Pi 3B (1 Go RAM) :

| Modèle | Taille | Vitesse estimée | Verdict |
|---|---|---|---|
| **Qwen2.5-0.5B-Instruct Q4_K_M** | ~400 Mo | ~2–4 tok/s | **recommandé** — meilleur compromis |
| SmolLM2-360M-Instruct Q4 | ~250 Mo | ~4–6 tok/s | plus rapide, tags plus grossiers |
| TinyLlama-1.1B-Chat Q4 | ~700 Mo | ~1–2 tok/s | trop lent et trop gros pour 1 Go |

Astuce mémoire : activez zram (`sudo apt install zram-tools`) pour absorber les
pics, et laissez le reste du Pi tranquille pendant les batchs.

Démarrez le serveur (l'unité systemd fournie le fait pour vous) :

```bash
~/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen2.5-0.5b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8081 -t 4 -c 1024
```

Sans LLM du tout, MoodStation reste fonctionnel : mettez `backend: "none"` dans
la config, les heuristiques suffisent pour démarrer.

## Utilisation

```bash
moodstation doctor                        # tout est-il OK ? (NAS, base, LLM)
moodstation sync                          # index + enrichissement + presets
moodstation generate "jazz calme pour dîner" --size 30
moodstation generate "énergie pour la course, 45 titres" --name "Running"
moodstation generate "musique triste pour un jour de pluie" --dry-run
moodstation stats                         # couverture de l'index
```

Dans DS Audio (téléphone, web ou desktop), vos playlists apparaissent avec le
préfixe configuré : **Mood • Détente**, **Mood • Énergie**, ou le nom que vous
avez donné. Chaque `sync` (planifié la nuit par systemd) les rafraîchit avec
les nouveaux morceaux et un contenu régénéré.

## Configuration

Tout passe par `config.yaml` (voir [`config.example.yaml`](config.example.yaml)
commenté) : connexion NAS, montage local, choix du backend LLM, taille et
préréglages des playlists, cadence d'enrichissement LLM par nuit.

## Performances attendues (honnêteté oblige)

- **Première sync** : indexation ~500 morceaux/min via API ; heuristiques
  instantanées → playlists utilisables immédiatement.
- **Enrichissement LLM** : ~30–60 s par morceau sur Pi 3B (Qwen 0.5B Q4).
  Avec 100 morceaux/nuit, une bibliothèque de 3 000 titres est entièrement
  raffinée en ~1 mois, sans jamais rien bloquer. Le batch est configurable.
- **Génération à la demande** : la traduction du prompt par le LLM prend
  ~20–40 s sur Pi 3B ; les heuristiques répondent en millisecondes ; la
  sélection SQL est instantanée.
- Le Pi 3B est le **plancher absolu** : sur Pi 4/5, tout devient 5 à 20 fois
  plus rapide avec le même logiciel.

## Dépannage

| Symptôme | Piste |
|---|---|
| « SYNO.API.Info injoignable » | URL du DSM (`http://IP:5000`) ? Service Web API activé ? |
| Erreur 403 au login | compte avec 2FA → créez un compte dédié sans 2FA |
| Erreur 105 | l'utilisateur n'a pas accès à Audio Station |
| Playlist créée mais invisible dans DS Audio | utilisez votre compte principal dans la config (les playlists sont personnelles) |
| LLM injoignable | `llama-server` lancé ? `moodstation doctor` ; sans LLM, tout marche en heuristiques |
| Pi instable pendant les batchs | activez zram, réduisez `batch_tracks`, vérifiez l'alimentation |

## Feuille de route

- [ ] Petite interface web locale (générer depuis un navigateur)
- [ ] Similarité sémantique entre morceaux (embeddings) pour le mode « radio »
- [ ] Analyse audio réelle (energie/BPM par essai, ex. librosa hors Pi 3B)
- [ ] Support DSM 7 (autres modèles de NAS)
- [ ] Image Docker pour NAS x86 puissants

## Licence

MIT — voir [LICENSE](LICENSE).
