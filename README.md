# Web Scraper Universel — CLI

Outil en ligne de commande Python pour extraire et archiver du contenu web. Il détecte automatiquement le type de site visité et adapte la stratégie de scraping en conséquence.

---

## Fonctionnalités

### Détection automatique du type de site

| Type détecté | Description |
|---|---|
| **Général** | Sites HTML classiques, blogs, wikis personnalisés |
| **MediaWiki** | Wikis (Fandom, Wikipedia, etc.) — accès via l'API officielle |
| **GitHub** | Dépôts GitHub — téléchargement de fichiers par l'API |
| **JS-lourd** | SPAs React/Vue/Angular — rendu complet via Playwright |

### 10 modes d'extraction (sites généraux)

| # | Mode | Sortie |
|---|---|---|
| 1 | **Texte propre** | Contenu lisible, mise en page préservée (tableaux, titres, listes) | `.txt` |
| 2 | **MHTML** | Page archivée complète, ouvrable dans Chrome/Edge | `.mhtml` |
| 3 | **Données structurées** | Tableaux, titres, paragraphes, images, listes | `.json` |
| 4 | **Images** | Toutes les images de la page | `images/` |
| 5 | **Arborescence URLs** | Carte de tous les liens avec leur hiérarchie | `.txt` |
| 6 | **Vidéos** | Fichiers vidéo directs + fallback yt-dlp (YouTube, Vimeo, TikTok…) | `videos/` |
| 7 | **Audios** | Fichiers audio détectés dans les balises et les scripts | `audios/` |
| 8 | **Documents** | PDF, Word, Excel, EPUB, CSV, XML… | `documents/` |
| 9 | **Archives** | ZIP, RAR, 7Z, ISO… | `archives/` |
| 10 | **Screenshot** | Capture d'écran pleine page via Playwright | `.png` |

Plusieurs modes sont sélectionnables simultanément (ex : `1,3,4`).

### Autres fonctionnalités

- **Crawl multi-pages** — suit les liens jusqu'à la profondeur souhaitée
- **Filtre d'extensions** — choisir quels formats garder (ex : uniquement `.mp4` et `.mkv`)
- **Filtre d'URL** — limiter le crawl aux URLs contenant un mot-clé (ex : `/articles/`)
- **Multi-URL** — saisir plusieurs URLs séparées par des virgules, ou un fichier `.txt`
- **Réglages en lot** — les options du premier scraping sont rejouées automatiquement pour les URLs suivantes
- **Cookies de session** — fichier `cookies.txt` pour les sites nécessitant une connexion
- **robots.txt** — option pour respecter les règles du site
- **Sessions persistantes** — reprendre un scraping interrompu, relancer, ou vérifier les nouveautés
- **Déduplication** — contenu identique non re-sauvegardé (hash MD5)

---

## Installation

### Dépendances requises

```bash
pip install requests beautifulsoup4 rich markdownify
```

### Dépendances optionnelles

```bash
# Pour : MHTML (mode 2), Screenshot (mode 10), sites JS-lourds
pip install playwright
python -m playwright install chromium

# Pour : téléchargement depuis YouTube, Vimeo, TikTok, etc. (mode 6)
pip install yt-dlp
```

---

## Utilisation

```bash
python scraper.py
```

Le programme guide interactivement à travers les options :

```
╔═════════════════════════════════════════════╗
║           WEB  SCRAPER  UNIVERSEL           ║
║   General · MediaWiki · GitHub · JS-lourd   ║
╚═════════════════════════════════════════════╝

  Dossier de destination [C:\Users\...\Downloads\Scraper] :
  > (Entrée pour garder le défaut)

  > https://exemple.com/articles

Que voulez-vous récupérer ? (plusieurs choix possibles)
  [ 1] Texte propre        — contenu lisible sans le code HTML (.txt)
  [ 2] MHTML               — page HTML archivée (.mhtml)
  [ 3] Données structurées — tableaux, titres, paragraphes (.json)
  ...
  [10] Screenshot          — capture d'écran pleine page (.png)

  Mode(s) (ex: 1  ou  1,3  pour plusieurs) : 1,3

  Profondeur de crawl (0 = page unique) : 0
```

### Entrées acceptées

| Format | Exemple |
|---|---|
| URL simple | `https://exemple.com/page` |
| Plusieurs URLs | `https://site1.com, https://site2.com` |
| Sans protocole | `exemple.com` → complété en `https://` |
| Fichier liste | `C:\Users\...\urls.txt` (une URL par ligne) |

### Reprise de session

Si une URL a déjà été scrapée, un menu apparaît :

```
  [1] Skipper             — passer cette URL
  [2] Recommencer         — nouveau dossier _1, _2...
  [3] Vérifier nouveautés — re-crawler, fichiers existants conservés
  [4] Vérifier concordance — comparer le contenu actuel avec le local (MD5)
  [5] Annuler             — arrêter le traitement
```

---

## Arborescence du projet

```
scrapercli_project/
│
├── scraper.py                  ← Point d'entrée — boucle principale, menus interactifs
│
└── scraper_modules/
    ├── __init__.py
    ├── detector.py             ← Détection du type de site (general / mediawiki / github / js)
    ├── downloader.py           ← Fetch HTTP/Playwright, extraction de contenu, listes d'extensions
    ├── crawler.py              ← Crawl récursif, logique par mode, téléchargements
    ├── exporter.py             ← Sauvegarde fichiers (txt, json, mhtml, bytes, csv)
    ├── mediawiki.py            ← API MediaWiki (wikitext, infobox, liens)
    ├── progress.py             ← Barre de progression Rich (transient)
    └── session.py              ← Persistance des sessions JSON (reprise, concordance)
```

---

## Arborescence des fichiers de sortie

L'URL scrapée est convertie en hiérarchie de dossiers.

**Exemple :** `https://planetpokemon.com/scarlet-and-violet/items`

```
Downloads/Scraper/
└── planetpokemon.com/
    └── scarlet-and-violet/
        └── items/
            ├── data/
            │   ├── items.txt           ← Mode 1 : texte propre
            │   ├── items.mhtml         ← Mode 2 : page archivée
            │   └── items.json          ← Mode 3 : données structurées
            ├── images/
            │   ├── item_0001.webp
            │   ├── item_0002.webp
            │   └── ...                 ← Mode 4 : images
            ├── videos/
            │   └── ...                 ← Mode 6 : vidéos
            ├── audios/
            │   └── ...                 ← Mode 7 : audios
            ├── documents/
            │   └── ...                 ← Mode 8 : documents
            └── archives/
                └── ...                 ← Mode 9 : archives
```

**Exemple avec crawl multi-pages** (profondeur 1) :

```
Downloads/Scraper/
└── planetpokemon.com/
    └── scarlet-and-violet/
        ├── items/
        │   └── data/
        │       ├── items.txt
        │       └── items.json
        ├── pokedex/
        │   └── data/
        │       └── pokedex.txt
        └── pokedex/
            └── quaxly/
                └── data/
                    └── quaxly.txt
```

**Exemple avec pagination** (même dossier, contenu différent) :

```
Downloads/Scraper/
└── site.com/
    └── articles/
        └── data/
            ├── articles.txt        ← page 1
            ├── articles_1.txt      ← page 2 (contenu différent)
            └── articles_2.txt      ← page 3
```

---

## Extensions supportées

| Mode | Extensions |
|---|---|
| **Images** | `.jpg` `.jpeg` `.png` `.gif` `.svg` `.webp` `.avif` `.tiff` `.tif` `.apng` `.bmp` `.ico` `.tga` |
| **Vidéos** | `.mp4` `.webm` `.mov` `.avi` `.mkv` `.m4v` `.ogv` `.flv` |
| **Audios** | `.mp3` `.wav` `.flac` `.aac` `.ogg` `.m4a` `.wma` `.opus` `.aiff` `.alac` `.mid` `.midi` |
| **Documents** | `.pdf` `.doc` `.docx` `.xls` `.xlsx` `.ppt` `.pptx` `.odt` `.ods` `.odp` `.epub` `.mobi` `.rtf` `.txt` `.xml` `.csv` |
| **Archives** | `.zip` `.rar` `.7z` `.tar` `.gz` `.bz2` `.tgz` `.tbz2` `.xz` `.iso` `.img` |

### Sites de streaming supportés (fallback yt-dlp)

YouTube · Vimeo · Twitch · Dailymotion · TikTok · Facebook · Instagram · X/Twitter · Bilibili · NicoVideo · Kick · Odysee · Reddit · Rumble · Streamable · IGN · GameSpot · Metacafe · TED · Loom
