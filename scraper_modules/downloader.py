# scraper_modules/downloader.py
import re, time, mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}
TIMEOUT = 20

IMAGE_EXTS = {
    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
    '.bmp', '.ico', '.tga', '.avif',
}
VIDEO_EXTS = {
    '.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv', '.m4v', '.flv',
}
AUDIO_EXTS = {
    '.mp3', '.wav', '.flac', '.aac', '.m4a', '.wma', '.opus', '.aiff',
}
DOCUMENT_EXTS = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.odt', '.ods', '.odp', '.epub', '.mobi',
}
ARCHIVE_EXTS = {
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.tgz', '.tbz2', '.xz',
}
ASSET_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOCUMENT_EXTS | ARCHIVE_EXTS | {
    '.css', '.js', '.pdf', '.txt', '.json', '.xml',
    '.mp3', '.zip', '.woff', '.woff2', '.ttf', '.otf',
}


def fetch(url: str, session: requests.Session = None) -> requests.Response:
    s = session or requests.Session()
    resp = s.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()
    return soup.get_text(separator='\n', strip=True)


def extract_structured(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.string.strip() if soup.title else ''
    headings = [h.get_text(strip=True) for h in soup.find_all(['h1', 'h2', 'h3', 'h4'])]
    paragraphs = [
        p.get_text(strip=True) for p in soup.find_all('p')
        if len(p.get_text(strip=True)) > 30
    ]
    images = list({urljoin(url, img['src']) for img in soup.find_all('img', src=True)})
    return {
        'url': url,
        'title': title,
        'headings': headings[:20],
        'paragraphs': paragraphs[:15],
        'images': images[:30],
    }


def extract_image_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    urls: set[str] = set()
    for img in soup.find_all('img', src=True):
        urls.add(urljoin(base_url, img['src']))
    for source in soup.find_all('source', srcset=True):
        for part in source['srcset'].split(','):
            src = part.strip().split()[0]
            if src:
                urls.add(urljoin(base_url, src))
    return [u for u in urls if Path(urlparse(u).path).suffix.lower() in IMAGE_EXTS]


def download_assets(html: str, base_url: str, dest: Path, session: requests.Session):
    """Télécharge CSS, JS et images liés à la page dans dest/."""
    soup = BeautifulSoup(html, 'html.parser')
    asset_urls: set[str] = set()
    for tag in soup.find_all(['img', 'script', 'link', 'source']):
        src = tag.get('src') or tag.get('href')
        if src:
            asset_urls.add(urljoin(base_url, src))
    dest.mkdir(parents=True, exist_ok=True)
    for url in asset_urls:
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in ASSET_EXTS:
            continue
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            raw_name = unquote(Path(urlparse(url).path).name or 'asset')
            p = Path(raw_name)
            fname = re.sub(r'[\\/*?:"<>|]', '_', p.stem)[:80] + re.sub(r'[\\/*?:"<>|]', '_', p.suffix)
            (dest / fname).write_bytes(r.content)
            time.sleep(0.2)
        except Exception:
            pass


VIDEO_PAGE_HOSTS = (
    'youtube.com/watch', 'youtu.be/',
    'vimeo.com/',
    'ign.com/videos/',
    'twitch.tv/',
    'dailymotion.com/video/',
    'streamable.com/',
    'rumble.com/',
)


def extract_video_page_urls(html: str, base_url: str) -> list[str]:
    """Trouve les liens vers des pages vidéo connues (YouTube, IGN /videos/, Vimeo...)."""
    soup = BeautifulSoup(html, 'html.parser')
    urls: set[str] = set()
    for a in soup.find_all('a', href=True):
        full = urljoin(base_url, a['href'].strip()).split('#')[0]
        if any(pat in full for pat in VIDEO_PAGE_HOSTS):
            urls.add(full)
    for m in re.finditer(r'https?://[^\s"\'<>\\]+', html):
        candidate = m.group(0).rstrip('.,;)')
        if any(pat in candidate for pat in VIDEO_PAGE_HOSTS):
            urls.add(candidate)
    return list(urls)


def extract_audio_urls(html: str, base_url: str) -> list[str]:
    urls: set[str] = set()
    soup = BeautifulSoup(html, 'html.parser')

    # Balises <audio> et <source> imbriquées
    for audio in soup.find_all('audio'):
        src = audio.get('src', '')
        if src and not src.startswith('blob:'):
            urls.add(urljoin(base_url, src))
        for source in audio.find_all('source', src=True):
            s = source['src']
            if not s.startswith('blob:'):
                urls.add(urljoin(base_url, s))

    # <source> standalone avec extension audio
    for source in soup.find_all('source', src=True):
        s = source['src']
        if not s.startswith('blob:') and Path(urlparse(s).path).suffix.lower() in AUDIO_EXTS:
            urls.add(urljoin(base_url, s))

    # <a href="..."> pointant vers un fichier audio
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if Path(urlparse(href).path).suffix.lower() in AUDIO_EXTS:
            urls.add(urljoin(base_url, href))

    # Scan regex dans le HTML brut (scripts, JSON embarqués)
    ext_pattern = '|'.join(re.escape(e) for e in AUDIO_EXTS)
    for m in re.finditer(
        rf'https?://[^\s"\'<>\\]+(?:{ext_pattern})(?:[^\s"\'<>\\]*)?',
        html, re.IGNORECASE
    ):
        candidate = m.group(0).rstrip('.,;)')
        if not re.search(r'https?://', candidate[8:]):
            urls.add(candidate)

    return list(urls)


def extract_video_urls(html: str, base_url: str) -> list[str]:
    urls: set[str] = set()

    # 1. Balises HTML classiques (video, source, a)
    soup = BeautifulSoup(html, 'html.parser')
    for video in soup.find_all('video'):
        src = video.get('src', '')
        if src and not src.startswith('blob:'):
            urls.add(urljoin(base_url, src))
        for source in video.find_all('source', src=True):
            s = source['src']
            if not s.startswith('blob:'):
                urls.add(urljoin(base_url, s))
    for source in soup.find_all('source', src=True):
        s = source['src']
        if not s.startswith('blob:') and Path(urlparse(s).path).suffix.lower() in VIDEO_EXTS:
            urls.add(urljoin(base_url, s))
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if Path(urlparse(href).path).suffix.lower() in VIDEO_EXTS:
            urls.add(urljoin(base_url, href))

    # 2. Scan regex dans le HTML brut — capte les URLs dans les scripts/JSON
    ext_pattern = '|'.join(re.escape(e) for e in VIDEO_EXTS)
    for m in re.finditer(
        rf'https?://[^\s"\'<>\\]+(?:{ext_pattern})(?:[^\s"\'<>\\]*)?',
        html, re.IGNORECASE
    ):
        candidate = m.group(0).rstrip('.,;)')
        if not re.search(r'https?://', candidate[8:]):
            urls.add(candidate)

    return list(urls)


def _extract_by_ext(html: str, base_url: str, exts: set[str]) -> list[str]:
    """Extrait les URLs pointant vers des fichiers dont l'extension est dans exts."""
    urls: set[str] = set()
    soup = BeautifulSoup(html, 'html.parser')

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if Path(urlparse(href).path).suffix.lower() in exts:
            full = urljoin(base_url, href)
            if urlparse(full).netloc:
                urls.add(full)

    ext_pattern = '|'.join(re.escape(e) for e in exts)
    for m in re.finditer(
        rf'https?://[^\s"\'<>\\]+(?:{ext_pattern})(?:[^\s"\'<>\\]*)?',
        html, re.IGNORECASE
    ):
        candidate = m.group(0).rstrip('.,;)')
        if re.search(r'https?://', candidate[8:]):
            continue
        # Vérifier que l'extension est bien en fin de chemin (pas .zip.asc, .tgz.sigstore...)
        if Path(urlparse(candidate).path).suffix.lower() not in exts:
            continue
        urls.add(candidate)

    return list(urls)


def extract_document_urls(html: str, base_url: str) -> list[str]:
    return _extract_by_ext(html, base_url, DOCUMENT_EXTS)


def extract_archive_urls(html: str, base_url: str) -> list[str]:
    return _extract_by_ext(html, base_url, ARCHIVE_EXTS)


def fetch_playwright(
    url: str,
    wait_selector: str = None,
    delay: int = 2,
    viewport: tuple = (1280, 720),
    cookies: list | None = None,
) -> str:
    """Retourne le HTML de la page après exécution du JavaScript."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright n'est pas installé.\n"
            "Exécutez : pip install playwright && python -m playwright install chromium"
        )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]})
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        page.goto(url, wait_until='networkidle', timeout=30000)
        if wait_selector:
            page.wait_for_selector(wait_selector, timeout=10000)
        if delay > 0:
            page.wait_for_timeout(delay * 1000)
        html = page.content()
        browser.close()
    return html


def screenshot_playwright(
    url: str,
    dest: Path,
    wait_selector: str = None,
    delay: int = 2,
    viewport: tuple = (1280, 720),
    cookies: list | None = None,
) -> Path:
    """Prend un screenshot pleine page avec Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright n'est pas installé.\n"
            "Exécutez : pip install playwright && python -m playwright install chromium"
        )
    dest.mkdir(parents=True, exist_ok=True)
    fname = re.sub(r'[\\/*?:"<>|]', '_', Path(urlparse(url).path).name or 'page') + '.png'
    out_path = dest / fname
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]})
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        page.goto(url, wait_until='networkidle', timeout=30000)
        if wait_selector:
            page.wait_for_selector(wait_selector, timeout=10000)
        if delay > 0:
            page.wait_for_timeout(delay * 1000)
        page.screenshot(path=str(out_path), full_page=True)
        browser.close()
    return out_path
