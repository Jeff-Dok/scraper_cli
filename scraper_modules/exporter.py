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
    """Fallback MHTML (RFC 2557) : URLs absolues + quoted-printable. Utilisé si Playwright indisponible."""
    import quopri
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin as _urljoin

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if path.exists():
        return path

    safe_url = url.replace('\r', '').replace('\n', '')
    base_scheme = urlparse(url).scheme

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(True):
        for attr in ('href', 'src', 'action'):
            val = (tag.get(attr) or '').strip()
            if not val or val.startswith(('http://', 'https://', 'data:', 'blob:', '#', 'javascript:')):
                continue
            tag[attr] = f'{base_scheme}:{val}' if val.startswith('//') else _urljoin(url, val)
        srcset = (tag.get('srcset') or '').strip()
        if srcset:
            chunks = []
            for chunk in srcset.split(','):
                pieces = chunk.strip().split()
                if pieces:
                    v = pieces[0]
                    if not v.startswith(('http://', 'https://', 'data:', 'blob:')):
                        v = f'{base_scheme}:{v}' if v.startswith('//') else _urljoin(url, v)
                    pieces[0] = v
                chunks.append(' '.join(pieces))
            tag['srcset'] = ', '.join(chunks)
    html_abs = str(soup)

    html_qp = quopri.encodestring(html_abs.encode('utf-8')).decode('ascii').replace('\n', '\r\n')
    boundary = f'----=_NextPart_{hashlib.md5(html_abs.encode()).hexdigest()[:12]}'

    content = b''.join([
        b'From: <Saved by scraper>\r\n',
        f'Snapshot-Content-Location: {safe_url}\r\n'.encode(),
        b'MIME-Version: 1.0\r\n',
        f'Content-Type: multipart/related; type="text/html"; boundary="{boundary}"\r\n'.encode(),
        b'\r\n',
        f'--{boundary}\r\n'.encode(),
        b'Content-Type: text/html; charset=UTF-8\r\n',
        b'Content-Transfer-Encoding: quoted-printable\r\n',
        f'Content-Location: {safe_url}\r\n'.encode(),
        b'\r\n',
        html_qp.encode('ascii'),
        b'\r\n--',
        boundary.encode(),
        b'--\r\n',
    ])
    path.write_bytes(content)
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