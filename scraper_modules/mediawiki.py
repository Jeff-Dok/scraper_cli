# scraper_modules/mediawiki.py
import re, time
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup

from .downloader import HEADERS, TIMEOUT


def detect_api_url(page_url: str) -> str:
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path
    if '/wiki/' in path:
        return f"{base}/api.php"
    if '/w/' in path:
        return f"{base}/w/api.php"
    return f"{base}/api.php"


def extract_title(page_url: str) -> str:
    parsed = urlparse(page_url)
    path = parsed.path
    for prefix in ('/wiki/', '/w/'):
        if prefix in path:
            return unquote(path.split(prefix, 1)[1].replace('_', ' '))
    return unquote(path.rstrip('/').split('/')[-1].replace('_', ' '))


def fetch_wikitext(page_url: str, session: requests.Session | None = None) -> str:
    """Récupère le wikitext brut d'une page MediaWiki."""
    _session = session or requests.Session()
    parsed = urlparse(page_url)
    if '/wiki/' in parsed.path:
        raw_url = page_url.replace('/wiki/', '/w/index.php?title=') + '&action=raw'
    elif 'index.php' in parsed.path:
        raw_url = page_url + ('&action=raw' if 'action=raw' not in page_url else '')
    else:
        raw_url = page_url

    try:
        resp = _session.get(raw_url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        content = resp.text
    except requests.HTTPError as e:
        if e.response.status_code == 404 and '/w/' in raw_url:
            raw_url = raw_url.replace('/w/', '/wiki/')
            resp = _session.get(raw_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            content = resp.text
        else:
            raise

    # Suit les redirections internes MediaWiki (#REDIRECT [[Titre]])
    match = re.match(r'#REDIRECT\s*\[\[(.*?)\]\]', content, re.IGNORECASE)
    if match:
        new_title = match.group(1).strip().replace(' ', '_')
        base = raw_url.split('index.php')[0]
        raw_url = f"{base}index.php?title={new_title}&action=raw"
        resp = _session.get(raw_url, headers=HEADERS, timeout=TIMEOUT)
        content = resp.text

    return content


def _api_get(api_url: str, params: dict, session: requests.Session) -> dict:
    params.setdefault('format', 'json')
    params.setdefault('formatversion', '2')
    resp = session.get(api_url, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_structured(page_url: str, session: requests.Session) -> dict:
    """Extrait infobox, description et images via l'API MediaWiki officielle."""
    api_url = detect_api_url(page_url)
    title = extract_title(page_url)

    data = _api_get(api_url, {
        'action': 'parse',
        'page': title,
        'prop': 'text|images|displaytitle',
    }, session)

    parse = data.get('parse', {})
    if not parse:
        return {}

    html = parse.get('text', '')
    soup = BeautifulSoup(html, 'html.parser')
    parsed_api = urlparse(api_url)
    base = f"{parsed_api.scheme}://{parsed_api.netloc}"

    result = {
        'title': re.sub(r'<[^>]+>', '', parse.get('displaytitle') or title),
        'url': f"{base}/wiki/{title.replace(' ', '_')}",
        'infobox': {},
        'description': '',
        'images': [],
    }

    # Infobox portable (Fandom moderne)
    aside = soup.find('aside', class_=lambda x: x and 'portable-infobox' in x)
    if aside:
        infobox: dict[str, str] = {}
        title_el = aside.find(class_=lambda x: x and 'pi-title' in x)
        if title_el:
            infobox['_name'] = title_el.get_text(strip=True)
        for item in aside.find_all(class_=lambda x: x and 'pi-data' in x):
            label = item.find(class_=lambda x: x and 'pi-data-label' in x)
            value = item.find(class_=lambda x: x and 'pi-data-value' in x)
            if label and value:
                infobox[label.get_text(strip=True)] = value.get_text(' ', strip=True)
        result['infobox'] = infobox

    # Fallback wikitable classique
    if not result['infobox']:
        table = soup.find('table', class_=lambda x: x and 'infobox' in x)
        if table:
            infobox = {}
            for row in table.find_all('tr'):
                cells = row.find_all(['th', 'td'])
                if len(cells) == 2:
                    k = cells[0].get_text(strip=True)
                    v = cells[1].get_text(' ', strip=True)
                    if k and v:
                        infobox[k] = v
            result['infobox'] = infobox

    # Description (premier paragraphe substantiel)
    content_div = soup.find(class_=lambda x: x and 'mw-parser-output' in x)
    if content_div:
        for tag in content_div.find_all(['aside', 'table', 'script', 'style']):
            tag.decompose()
        paras = [p.get_text(' ', strip=True) for p in content_div.find_all('p')
                 if len(p.get_text(strip=True)) > 30]
        if paras:
            result['description'] = paras[0]

    # Image principale
    img_names = [n for n in parse.get('images', [])
                 if n.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'))]
    if img_names:
        img_url = _resolve_image(api_url, img_names[0], session)
        if img_url:
            result['images'].append(img_url)

    return result


def _resolve_image(api_url: str, filename: str, session: requests.Session) -> str:
    data = _api_get(api_url, {
        'action': 'query',
        'titles': f'File:{filename}',
        'prop': 'imageinfo',
        'iiprop': 'url',
        'iilimit': '1',
    }, session)
    for page in data.get('query', {}).get('pages', []):
        info = page.get('imageinfo', [])
        if info:
            return info[0].get('url', '')
    return ''


def get_links(api_url: str, title: str, session: requests.Session, title_filter: str = '') -> list[str]:
    """Retourne les titres des pages liées (namespace 0 seulement)."""
    SKIP = ('Special:', 'Talk:', 'User:', 'File:', 'Template:', 'Help:', 'Category:')
    links: list[str] = []
    params = {
        'action': 'query',
        'titles': title,
        'prop': 'links',
        'pllimit': '500',
        'plnamespace': '0',
    }
    while True:
        data = _api_get(api_url, params, session)
        for page in data.get('query', {}).get('pages', []):
            for link in page.get('links', []):
                t = link.get('title', '')
                if t and not any(t.startswith(p) for p in SKIP):
                    if not title_filter or title_filter.lower() in t.lower():
                        links.append(t)
        cont = data.get('continue', {})
        if 'plcontinue' not in cont:
            break
        params['plcontinue'] = cont['plcontinue']
        time.sleep(0.4)
    return links
