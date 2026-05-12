# scraper_modules/detector.py
from urllib.parse import urlparse

MEDIAWIKI_DOMAINS = {
    'wikipedia.org', 'wikimedia.org', 'wiktionary.org',
    'uesp.net', 'fandom.com', 'wikia.com', 'minecraft.wiki',
    'terraria.wiki.gg', 'zelda.wiki.gg', 'wowpedia.fandom.com',
}
MEDIAWIKI_PATHS = ('/wiki/', '/w/index.php')


def detect(url: str) -> str:
    """Retourne : 'mediawiki' | 'github' | 'js' | 'general'"""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if 'github.com' in host:
        return 'github'

    for domain in MEDIAWIKI_DOMAINS:
        if domain in host:
            return 'mediawiki'

    for marker in MEDIAWIKI_PATHS:
        if marker in path:
            return 'mediawiki'

    return 'general'


def is_js_heavy(url: str) -> bool:
    """Heuristique légère : page HTML quasi-vide → probablement une SPA JS."""
    try:
        import requests
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if 'text/html' in resp.headers.get('Content-Type', ''):
            return len(resp.text.strip()) < 1500
    except Exception:
        pass
    return False
