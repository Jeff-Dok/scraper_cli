# scraper_modules/exporter.py
import os, json, csv, re, subprocess, sys, hashlib
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_DEST = Path.home() / "Downloads/Scraper"


def url_to_folder(url: str) -> str:
    name = re.sub(r'^https?://', '', url)
    name = re.sub(r'[?#].*$', '', name)
    name = name.rstrip('/')
    name = name.replace('/', '_')
    name = re.sub(r'[\\*?"<>|]', '_', name)
    return name[:100] or 'page'


def url_to_page_dest(url: str, base_dest: Path) -> tuple[Path, str]:
    """Transforme une URL en (page_folder, page_name) selon la hiérarchie de chemin.

    https://planetpokemon.com/scarlet-and-violet/pokedex/quaxly
    → (base_dest/planetpokemon.com/scarlet-and-violet/pokedex/quaxly, "quaxly")

    https://planetpokemon.com/scarlet-and-violet/items/?page=2
    → (base_dest/planetpokemon.com/scarlet-and-violet/items/page=2, "page=2")

    https://ldvelh.ezael.net/?dir=Loup%20Solitaire
    → (base_dest/ldvelh.ezael.net/dir=Loup Solitaire, "dir=Loup Solitaire")

    https://planetpokemon.com/
    → (base_dest/planetpokemon.com, "planetpokemon.com")
    """
    parsed = urlparse(url)
    netloc = re.sub(r'[\\/*?:"<>|]', '_', parsed.netloc) or 'unknown'

    raw_parts = [p for p in parsed.path.strip('/').split('/') if p]
    parts = [re.sub(r'[\\/*?:"<>|]', '_', unquote(p))[:80] for p in raw_parts]

    if parsed.query:
        query_part = re.sub(r'[\\/*?:"<>|]', '_', unquote(parsed.query))[:80]
        parts.append(query_part)

    if not parts:
        return base_dest / netloc, netloc

    page_name = parts[-1]
    page_folder = base_dest / netloc
    for part in parts:
        page_folder = page_folder / part

    return page_folder, page_name


def safe_name(url: str) -> str:
    name = re.split(r'[/=]', url.rstrip('/'))[-1] or "page"
    name = unquote(name)
    return re.sub(r'[\\/*?:"<>|]', '_', name)[:80]


def unique_path(folder: Path, filename: str) -> Path:
    path = folder / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while path.exists():
        path = folder / f"{stem}_{i}{suffix}"
        i += 1
    return path


def save_text(content: str, folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / filename
    if base.exists() and base.read_text(encoding='utf-8', errors='replace') == content:
        return base  # contenu identique — déjà sauvegardé
    path = unique_path(folder, filename)
    path.write_text(content, encoding='utf-8')
    return path


def save_bytes(content: bytes, folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / filename
    if base.exists():
        return base
    path = unique_path(folder, filename)
    path.write_bytes(content)
    return path


def save_mhtml(html: str, url: str, folder: Path, filename: str) -> Path:
    """Sauvegarde le HTML sous forme MHTML simple (RFC 2557, sans ressources embarquées).

    Comportement snapshot : si le fichier existe déjà, il est retourné tel quel
    sans vérification de contenu (contrairement à save_text/save_json).
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if path.exists():
        return path
    safe_url = url.replace('\r', '').replace('\n', '')
    boundary = f'----=_NextPart_{hashlib.md5(html.encode()).hexdigest()[:12]}'
    content = (
        f'MIME-Version: 1.0\r\n'
        f'Content-Type: multipart/related; type="text/html"; boundary="{boundary}"\r\n'
        f'Snapshot-Content-Location: {safe_url}\r\n'
        f'\r\n'
        f'--{boundary}\r\n'
        f'Content-Type: text/html; charset=UTF-8\r\n'
        f'Content-Transfer-Encoding: 8bit\r\n'
        f'Content-Location: {safe_url}\r\n'
        f'\r\n'
        f'{html}\r\n'
        f'\r\n'
        f'--{boundary}--\r\n'
    )
    path.write_text(content, encoding='utf-8')
    return path


def save_json(data: dict | list, folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    new_content = json.dumps(data, ensure_ascii=False, indent=2)
    base = folder / filename
    if base.exists() and base.read_text(encoding='utf-8') == new_content:
        return base  # contenu identique — déjà sauvegardé
    path = unique_path(folder, filename)
    path.write_text(new_content, encoding='utf-8')
    return path


def save_csv(data: list[dict], folder: Path, filename: str) -> Path | None:
    if not data:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, filename)
    keys: set[str] = set()
    for item in data:
        keys.update(k for k in item if k != 'infobox')
        keys.update(item.get('infobox', {}).keys())
    fieldnames = sorted(keys)
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for item in data:
            row = {k: v for k, v in item.items() if k != 'infobox'}
            row.update(item.get('infobox', {}))
            writer.writerow(row)
    return path


def open_folder(folder: Path):
    if sys.platform == 'win32':
        os.startfile(str(folder))
    elif sys.platform == 'darwin':
        subprocess.run(['open', str(folder)])
    else:
        subprocess.run(['xdg-open', str(folder)])