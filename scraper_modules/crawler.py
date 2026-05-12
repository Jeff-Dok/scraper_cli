# scraper_modules/crawler.py
import hashlib, re, time, urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote, parse_qs

import requests
from bs4 import BeautifulSoup

from .downloader import (fetch, extract_text, extract_structured,
                         extract_image_urls, extract_video_urls, extract_audio_urls,
                         extract_document_urls, extract_archive_urls,
                         download_assets, HEADERS, TIMEOUT)
from .exporter import save_text, save_json, save_csv, save_bytes, url_to_page_dest, save_mhtml
from .session import is_partial, MIN_SIZE_VIDEO_AUDIO, MIN_SIZE_DOC_ARCHIVE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '_', name)[:80]


def _safe_filename(name: str) -> str:
    """Comme _safe(), mais préserve l'extension lors de la troncature."""
    p = Path(name)
    stem = re.sub(r'[\\/*?:"<>|]', '_', p.stem)[:80]
    ext = re.sub(r'[\\/*?:"<>|]', '_', p.suffix)
    return stem + ext


def _extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    links: set[str] = set()
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if href.startswith(('#', 'javascript:', 'mailto:')):
            continue
        full = urljoin(base_url, href).split('#')[0]
        if full.startswith('http'):
            links.add(full)
    return list(links)


def _same_domain(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


def _is_redirect(html: str, text: str) -> bool:
    if 'class="redirectmsg"' in html.lower() or 'id="redirectsub"' in html.lower():
        return True
    return text.strip().upper().startswith('#REDIRECT')


def _download_images(img_urls: list[str], dest: Path, session: requests.Session,
                     image_urls_seen: set | None = None,
                     ext_filter: set | None = None,
                     log: callable | None = None) -> int:
    _log = log or print
    if image_urls_seen is None:
        image_urls_seen = set()
    count = 0
    for img_url in img_urls:
        if ext_filter:
            ext = Path(urlparse(img_url).path).suffix.lower()
            if ext not in ext_filter:
                continue
        if img_url in image_urls_seen:
            continue
        try:
            r = session.get(img_url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            image_urls_seen.add(img_url)
            fname = _safe_filename(unquote(Path(urlparse(img_url).path).name or 'img'))
            if (dest / fname).exists():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            (dest / fname).write_bytes(r.content)
            count += 1
            time.sleep(0.2)
        except Exception:
            pass
    return count


# ── Helpers vidéo ────────────────────────────────────────────────────────────

# Pattern CDN adaptatif : {hash}-{bitrate}-{id}.mp4  ou  {hash}-{bitrate}-{id}-w.mp4
_BITRATE_IN_FNAME = re.compile(r'^(.+?)-(\d{5,})-(.+\.\w+)$')


def _canonical_video_key(url: str) -> str:
    """Clé canonique pour déduplication cross-pages et cross-qualités.

    Pour les URLs CDN de type {hash}-{bitrate}-{id}.mp4, supprime le bitrate
    du nom de fichier afin que toutes les variantes d'une même vidéo aient
    la même clé. Retourne le fname brut pour les autres patterns.
    """
    fname = _safe_filename(unquote(Path(urlparse(url).path).name or 'video'))
    m = _BITRATE_IN_FNAME.match(fname)
    if m:
        return f"{m.group(1)}-{m.group(3)}"
    return fname


def _best_quality_urls(video_urls: list[str]) -> list[str]:
    """Pour chaque vidéo, garde uniquement l'URL de la meilleure qualité.

    Détecte deux patterns :
    - Bitrate dans le nom de fichier : {hash}-{bitrate}-{id}.mp4 (streaming adaptatif CDN)
    - Qualité dans le chemin URL : /1920/video.mp4
    """
    def _score(url: str, fname: str) -> int:
        m = _BITRATE_IN_FNAME.match(fname)
        if m:
            return int(m.group(2))
        parts = urlparse(url).path.split('/')
        if len(parts) >= 2 and parts[-2].isdigit():
            return int(parts[-2])
        nums = re.findall(r'/(\d{3,4})/', url)
        return max((int(n) for n in nums), default=0)

    by_canonical: dict[str, tuple[str, int]] = {}
    for url in video_urls:
        fname = _safe_filename(unquote(Path(urlparse(url).path).name or 'video'))
        key = _canonical_video_key(url)
        score = _score(url, fname)
        if key not in by_canonical or score > by_canonical[key][1]:
            by_canonical[key] = (url, score)
    return [url for url, _ in by_canonical.values()]


def _download_videos(video_urls: list[str], dest: Path, session: requests.Session,
                     video_urls_seen: set | None = None,
                     ext_filter: set | None = None,
                     progress=None, log: callable | None = None) -> int:
    """Télécharge les vidéos trouvées directement dans le HTML."""
    _log = log or print
    if video_urls_seen is None:
        video_urls_seen = set()
    if ext_filter:
        video_urls = [u for u in video_urls
                      if Path(urlparse(u).path).suffix.lower() in ext_filter]
    best = _best_quality_urls(video_urls)
    count = 0
    for url in best:
        canonical = _canonical_video_key(url)
        if canonical in video_urls_seen:
            continue
        try:
            fname = _safe_filename(unquote(Path(urlparse(url).path).name or 'video'))
            if not fname or fname == '_':
                fname = f"video_{count+1}"
            out = dest / fname
            if out.exists():
                if is_partial(out, content_length=None, min_size=MIN_SIZE_VIDEO_AUDIO):
                    out.unlink(missing_ok=True)
                else:
                    if not progress:
                        _log(f"    [skip] {fname} (déjà téléchargé)")
                    video_urls_seen.add(canonical)
                    continue
            parts = urlparse(url).path.split('/')
            quality = f"  [{parts[-2]}px]" if len(parts) >= 2 and parts[-2].isdigit() else ""
            r = session.get(url, headers=HEADERS, timeout=60, stream=True)
            r.raise_for_status()
            dest.mkdir(parents=True, exist_ok=True)
            content_length = int(r.headers.get('Content-Length', 0)) or None
            task_file = progress.add_task(fname, total=content_length) if progress else None
            with open(out, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
                    if progress and task_file is not None:
                        progress.advance(task_file, len(chunk))
            if progress and task_file is not None:
                progress.remove_task(task_file)
            video_urls_seen.add(canonical)
            if not progress:
                _log(f"    ✅ {fname}{quality}")
            count += 1
            time.sleep(0.3)
        except Exception as e:
            _log(f"    ✗ {url}: {e}")
    return count


def _download_audios(audio_urls: list[str], dest: Path, session: requests.Session,
                     audio_urls_seen: set | None = None,
                     ext_filter: set | None = None,
                     progress=None, log: callable | None = None) -> int:
    """Télécharge les fichiers audio trouvés directement dans le HTML."""
    _log = log or print
    if audio_urls_seen is None:
        audio_urls_seen = set()
    count = 0
    for url in audio_urls:
        if ext_filter:
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in ext_filter:
                continue
        if url in audio_urls_seen:
            continue
        try:
            fname = _safe_filename(unquote(Path(urlparse(url).path).name or 'audio'))
            if not fname or fname == '_':
                fname = f"audio_{count + 1}"
            out = dest / fname
            if out.exists():
                if is_partial(out, content_length=None, min_size=MIN_SIZE_VIDEO_AUDIO):
                    out.unlink(missing_ok=True)
                else:
                    if not progress:
                        _log(f"    [skip] {fname} (déjà téléchargé)")
                    audio_urls_seen.add(url)
                    continue
            r = session.get(url, headers=HEADERS, timeout=60, stream=True)
            r.raise_for_status()
            dest.mkdir(parents=True, exist_ok=True)
            content_length = int(r.headers.get('Content-Length', 0)) or None
            task_file = progress.add_task(fname, total=content_length) if progress else None
            with open(out, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
                    if progress and task_file is not None:
                        progress.advance(task_file, len(chunk))
            if progress and task_file is not None:
                progress.remove_task(task_file)
            audio_urls_seen.add(url)
            if not progress:
                _log(f"    ✅ {fname}")
            count += 1
            time.sleep(0.3)
        except Exception as e:
            _log(f"    ✗ {url}: {e}")
    return count


def _download_files(urls: list[str], dest: Path, session: requests.Session,
                    seen: set, ext_filter: set | None, label: str,
                    progress=None, log: callable | None = None) -> int:
    """Téléchargement générique streaming pour documents et archives."""
    _log = log or print
    count = 0
    for url in urls:
        if ext_filter:
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in ext_filter:
                continue
        if url in seen:
            continue
        try:
            fname = _safe_filename(unquote(Path(urlparse(url).path).name or label))
            if not fname or fname == '_':
                fname = f"{label}_{count + 1}"
            out = dest / fname
            if out.exists():
                if is_partial(out, content_length=None, min_size=MIN_SIZE_DOC_ARCHIVE):
                    out.unlink(missing_ok=True)
                else:
                    seen.add(url)
                    continue
            r = session.get(url, headers=HEADERS, timeout=120, stream=True)
            r.raise_for_status()
            dest.mkdir(parents=True, exist_ok=True)
            content_length = int(r.headers.get('Content-Length', 0)) or None
            task_file = progress.add_task(fname, total=content_length) if progress else None
            with open(out, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
                    if progress and task_file is not None:
                        progress.advance(task_file, len(chunk))
            if progress and task_file is not None:
                progress.remove_task(task_file)
            seen.add(url)
            if not progress:
                _log(f"    ✅ {fname}")
            count += 1
            time.sleep(0.3)
        except Exception as e:
            _log(f"    ✗ {url}: {e}")
    return count


def _download_documents(doc_urls, dest, session, seen=None, ext_filter=None, progress=None, log: callable | None = None):
    if seen is None:
        seen = set()
    return _download_files(doc_urls, dest, session, seen, ext_filter, 'document', progress, log)


def _download_archives(arc_urls, dest, session, seen=None, ext_filter=None, progress=None, log: callable | None = None):
    if seen is None:
        seen = set()
    return _download_files(arc_urls, dest, session, seen, ext_filter, 'archive', progress, log)


def _get_robots_parser(base_url: str, session: requests.Session) -> urllib.robotparser.RobotFileParser:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        resp = session.get(robots_url, headers=HEADERS, timeout=TIMEOUT)
        rp.parse(resp.text.splitlines())
    except Exception:
        pass
    return rp


def _download_ytdlp(url: str, dest: Path, video_urls_seen: set | None = None,
                    log: callable | None = None) -> bool:
    """Télécharge via yt-dlp si disponible. Retourne True si succès."""
    _log = log or print
    if video_urls_seen is not None and url in video_urls_seen:
        return False
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        _log("  ⚠ yt-dlp non installé — installez avec : pip install yt-dlp")
        return False
    try:
        import yt_dlp
        opts = {
            'outtmpl': str(dest / '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        if video_urls_seen is not None:
            video_urls_seen.add(url)
        return True
    except Exception as e:
        _log(f"  ✗ yt-dlp : {e}")
        return False


# ── Helpers arborescence d'URLs ───────────────────────────────────────────────

def _build_url_tree(
    url: str,
    depth: int,
    session: requests.Session,
    visited: set | None = None,
    url_filter: str = '',
    current_depth: int = 0,
    delay: float = 0.3,
) -> dict:
    """Construit un arbre {url: {child_url: {...}}} récursivement."""
    if visited is None:
        visited = set()
    if url in visited or current_depth > depth:
        return {}
    visited.add(url)

    node: dict = {}
    if current_depth < depth:
        try:
            resp = fetch(url, session)
            links = _extract_links(resp.text, url)
            for child in links:
                if child in visited:
                    continue
                if not _same_domain(child, url):
                    continue
                if url_filter and url_filter.lower() not in child.lower():
                    continue
                time.sleep(delay)
                node[child] = _build_url_tree(
                    child, depth, session, visited, url_filter,
                    current_depth + 1, delay,
                )
        except Exception:
            pass
    return node


def _render_tree(node: dict, prefix: str = '', url: str = '') -> list[str]:
    """Transforme l'arbre en liste de lignes texte avec caractères arborescence."""
    lines = []
    children = list(node.items())
    for i, (child_url, subtree) in enumerate(children):
        is_last = (i == len(children) - 1)
        connector = '└── ' if is_last else '├── '
        lines.append(f"{prefix}{connector}{child_url}")
        extension = '    ' if is_last else '│   '
        lines.extend(_render_tree(subtree, prefix + extension, child_url))
    return lines


def map_urls(
    url: str,
    dest: Path,
    depth: int,
    session: requests.Session,
    url_filter: str = '',
    log: callable | None = None,
) -> None:
    """Construit et sauvegarde l'arborescence d'URLs dans un fichier .txt."""
    _log = log or print
    _log('  Analyse des liens en cours...')
    tree = _build_url_tree(url, depth, session, url_filter=url_filter)
    lines = [url]
    lines.extend(_render_tree(tree))
    content = '\n'.join(lines)
    page_folder, page_name = url_to_page_dest(url, dest)
    path = save_text(content, page_folder / 'data', f"{page_name}_urls.txt")
    total = content.count('\n')
    _log(f"  ✅ {total} URL(s) → {path.name}")


# ── Crawl général ─────────────────────────────────────────────────────────────

def crawl(
    url: str,
    modes: list[int],
    dest: Path,
    depth: int,
    session: requests.Session,
    visited: set,
    delay: float = 0.3,
    current_depth: int = 0,
    use_playwright: bool = False,
    playwright_opts: dict | None = None,
    url_filter: str = '',
    content_hashes: set | None = None,
    image_urls_seen: set | None = None,
    video_urls_seen: set | None = None,
    audio_urls_seen: set | None = None,
    doc_urls_seen: set | None = None,
    arc_urls_seen: set | None = None,
    img_ext_filter: set | None = None,
    vid_ext_filter: set | None = None,
    aud_ext_filter: set | None = None,
    doc_ext_filter: set | None = None,
    arc_ext_filter: set | None = None,
    respect_robots: bool = False,
    _robots_cache: dict | None = None,
    progress=None,
    task_url=None,
    session_data: dict | None = None,
    sessions_dir: Path | None = None,
    log: callable | None = None,
    cancel_event=None,          # ← nouveau
):
    if cancel_event is not None and cancel_event.is_set():
        return
    if url in visited or current_depth > depth:
        return
    visited.add(url)
    _log = log or print
    _log(f"🔍 {url}")
    if progress and task_url is not None:
        n = len(visited)
        label = "page visitée" if n == 1 else "pages visitées"
        progress.update(task_url, description=f"{urlparse(url).netloc} — {n} {label}")
    if session_data is not None:
        session_data['visited'] = list(visited)
        from .session import save_session, SESSIONS_DIR as _SD
        save_session(session_data, Path(sessions_dir) if sessions_dir is not None else _SD)
    if _robots_cache is None:
        _robots_cache = {}
    if respect_robots:
        netloc = urlparse(url).netloc
        if netloc not in _robots_cache:
            _robots_cache[netloc] = _get_robots_parser(url, session)
        if not _robots_cache[netloc].can_fetch('*', url):
            _log(f"  ⚠ Interdit par robots.txt : {url}")
            return
    if content_hashes is None:
        content_hashes = set()
    if image_urls_seen is None:
        image_urls_seen = set()
    if video_urls_seen is None:
        video_urls_seen = set()
    if audio_urls_seen is None:
        audio_urls_seen = set()
    if doc_urls_seen is None:
        doc_urls_seen = set()
    if arc_urls_seen is None:
        arc_urls_seen = set()

    try:
        if use_playwright:
            from .downloader import fetch_playwright
            html = fetch_playwright(url, **(playwright_opts or {}))
        else:
            resp = fetch(url, session)
            html = resp.text
            if resp.url != url:
                visited.add(resp.url)
    except Exception as e:
        _log(f"  ✗ {url} : {e}")
        return

    text = extract_text(html)
    page_folder, page_name = url_to_page_dest(url, dest)

    if _is_redirect(html, text):
        _log(f"  ⚠ Redirection, ignoré : {page_name}")
        active_modes: list[int] = []
    else:
        content_hash = hashlib.md5(text.encode()).hexdigest()
        if content_hash in content_hashes:
            _log(f"  ⚠ Contenu déjà sauvegardé, ignoré : {page_name}")
            active_modes = []
        else:
            content_hashes.add(content_hash)
            active_modes = modes

    for mode in active_modes:
        if mode == 1:
            path = save_text(text, page_folder / 'data', f"{page_name}.txt")
            _log(f"  ✅ data/{path.name}")

        elif mode == 2:
            path = save_mhtml(html, url, page_folder / 'data', f"{page_name}.mhtml")
            _log(f"  ✅ data/{path.name}")

        elif mode == 3:
            folder = page_folder / 'data'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'index.html').write_text(html, encoding='utf-8')
            download_assets(html, url, folder, session)
            _log(f"  ✅ data/ (HTML + assets)")

        elif mode == 4:
            path = save_json(extract_structured(html, url), page_folder / 'data', f"{page_name}.json")
            _log(f"  ✅ data/{path.name}")

        elif mode == 5:
            img_urls = extract_image_urls(html, url)
            count = _download_images(img_urls, page_folder / 'images', session,
                                     image_urls_seen, ext_filter=img_ext_filter, log=log)
            _log(f"  ✅ {count} image(s) → images/")

        elif mode == 7:
            from .downloader import extract_video_page_urls
            vid_urls = extract_video_urls(html, url)
            vid_dest = page_folder / 'videos'
            if vid_urls:
                count = _download_videos(vid_urls, vid_dest, session, video_urls_seen,
                                         ext_filter=vid_ext_filter, progress=progress, log=log)
                _log(f"  ✅ {count} vidéo(s) directe(s) → videos/")
            else:
                page_urls = extract_video_page_urls(html, url)
                if page_urls:
                    _log(f"  ⚠ Aucune vidéo directe — {len(page_urls)} page(s) vidéo détectée(s), tentative yt-dlp...")
                    success = 0
                    for vpage in page_urls:
                        _log(f"    → {vpage}")
                        if _download_ytdlp(vpage, vid_dest, video_urls_seen, log=log):
                            success += 1
                    if not success:
                        _log(f"  ✗ Aucune vidéo récupérée.")
                else:
                    _log(f"  ⚠ Aucune vidéo directe trouvée — tentative yt-dlp...")
                    if not _download_ytdlp(url, vid_dest, video_urls_seen, log=log):
                        _log(f"  ✗ Aucune vidéo récupérée.")

        elif mode == 8:
            aud_urls = extract_audio_urls(html, url)
            aud_dest = page_folder / 'audios'
            if aud_urls:
                count = _download_audios(aud_urls, aud_dest, session, audio_urls_seen,
                                         ext_filter=aud_ext_filter, progress=progress, log=log)
                _log(f"  ✅ {count} audio(s) → audios/")
            else:
                _log(f"  ⚠ Aucun audio trouvé sur cette page.")

        elif mode == 9:
            doc_urls = extract_document_urls(html, url)
            doc_dest = page_folder / 'documents'
            if doc_urls:
                count = _download_documents(doc_urls, doc_dest, session, doc_urls_seen,
                                            ext_filter=doc_ext_filter, progress=progress, log=log)
                _log(f"  ✅ {count} document(s) → documents/")
            else:
                _log(f"  ⚠ Aucun document trouvé sur cette page.")

        elif mode == 10:
            arc_urls = extract_archive_urls(html, url)
            arc_dest = page_folder / 'archives'
            if arc_urls:
                count = _download_archives(arc_urls, arc_dest, session, arc_urls_seen,
                                           ext_filter=arc_ext_filter, progress=progress, log=log)
                _log(f"  ✅ {count} archive(s) → archives/")
            else:
                _log(f"  ⚠ Aucune archive trouvée sur cette page.")

        elif mode == 11:
            try:
                from .downloader import screenshot_playwright
                pw_opts = playwright_opts or {'viewport': (1920, 1080), 'delay': 2}
                path = screenshot_playwright(url, page_folder / 'data', **pw_opts)
                _log(f"  ✅ data/{path.name}")
            except Exception as e:
                _log(f"  ✗ Screenshot: {e}")

    if current_depth < depth:
        for child_url in _extract_links(html, url):
            if child_url not in visited and _same_domain(child_url, url):
                if url_filter and url_filter.lower() not in child_url.lower():
                    continue
                time.sleep(delay)
                crawl(url=child_url, modes=modes, dest=dest, depth=depth,
                      session=session, visited=visited, delay=delay,
                      current_depth=current_depth + 1,
                      use_playwright=use_playwright, playwright_opts=playwright_opts,
                      url_filter=url_filter, content_hashes=content_hashes,
                      image_urls_seen=image_urls_seen, video_urls_seen=video_urls_seen,
                      audio_urls_seen=audio_urls_seen,
                      doc_urls_seen=doc_urls_seen, arc_urls_seen=arc_urls_seen,
                      img_ext_filter=img_ext_filter, vid_ext_filter=vid_ext_filter,
                      aud_ext_filter=aud_ext_filter,
                      doc_ext_filter=doc_ext_filter, arc_ext_filter=arc_ext_filter,
                      respect_robots=respect_robots, _robots_cache=_robots_cache,
                      progress=progress, task_url=task_url,
                      session_data=session_data, sessions_dir=sessions_dir,
                      log=log, cancel_event=cancel_event)


# ── Crawl MediaWiki ───────────────────────────────────────────────────────────

def crawl_mediawiki(
    page_url: str,
    modes: list[int],
    dest: Path,
    depth: int,
    session: requests.Session,
    visited_titles: set,
    title_filter: str = '',
    do_csv: bool = False,
    delay: float = 0.4,
    current_depth: int = 0,
    _all_structured: list | None = None,
    log: callable | None = None,
):
    _log = log or print
    from .mediawiki import (detect_api_url, extract_title, fetch_wikitext,
                            fetch_structured, get_links)

    title = extract_title(page_url)
    if title in visited_titles:
        return
    visited_titles.add(title)

    if _all_structured is None:
        _all_structured = []

    api_url = detect_api_url(page_url)
    safe = _safe(title.replace(' ', '_'))
    page_dest = dest / safe
    page_dest.mkdir(parents=True, exist_ok=True)

    indent = '  ' * current_depth
    _log(f"{indent}[→] {title}")

    for mode in modes:
        try:
            if mode == 1:
                wikitext = fetch_wikitext(page_url, session)
                path = save_text(wikitext, page_dest, f"{safe}.txt")
                _log(f"{indent}  ✅ Wikitext: {path.name}")

            elif mode == 2:
                data = fetch_structured(page_url, session)
                if data:
                    path = save_json(data, page_dest, f"{safe}.json")
                    _log(f"{indent}  ✅ JSON: {path.name}")
                    _all_structured.append(data)

            elif mode == 3:
                resp = fetch(page_url, session)
                path = save_text(resp.text, page_dest, f"{safe}.html")
                _log(f"{indent}  ✅ HTML: {path.name}")

            elif mode == 4:
                resp = fetch(page_url, session)
                path = save_text(extract_text(resp.text), page_dest, f"{safe}.txt")
                _log(f"{indent}  ✅ Texte: {path.name}")

            elif mode == 5:
                resp = fetch(page_url, session)
                img_urls = extract_image_urls(resp.text, page_url)
                count = _download_images(img_urls, page_dest / 'images', session, log=log)
                _log(f"{indent}  ✅ {count} image(s)")

        except Exception as e:
            _log(f"{indent}  ✗ Mode {mode}: {e}")

    time.sleep(delay)

    if current_depth < depth:
        children = sorted(set(get_links(api_url, title, session, title_filter)) - visited_titles)
        _log(f"{indent}  ↪ {len(children)} liens")
        parsed_api = urlparse(api_url)
        base = f"{parsed_api.scheme}://{parsed_api.netloc}"
        for child_title in children:
            child_url = f"{base}/wiki/{child_title.replace(' ', '_')}"
            time.sleep(delay)
            crawl_mediawiki(
                page_url=child_url, modes=modes, dest=dest, depth=depth,
                session=session, visited_titles=visited_titles,
                title_filter=title_filter, do_csv=do_csv, delay=delay,
                current_depth=current_depth + 1, _all_structured=_all_structured,
                log=log,
            )

    # Export CSV global une seule fois (au niveau racine)
    if current_depth == 0 and do_csv and _all_structured:
        csv_path = save_csv(_all_structured, dest, 'all.csv')
        if csv_path:
            _log(f"  📊 CSV → {csv_path.name}")


# ── Crawl GitHub ──────────────────────────────────────────────────────────────

def crawl_github(
    url: str,
    dest: Path,
    depth: int,
    session: requests.Session,
    ext_filter: list[str] | None = None,
    log: callable | None = None,
):
    parsed = urlparse(url)
    parts = parsed.path.strip('/').split('/')
    if len(parts) < 2:
        _log = log or print
        _log(f"  ✗ URL GitHub invalide: {url}")
        return

    owner, repo = parts[0], parts[1]
    if len(parts) > 3 and parts[2] in ('tree', 'blob'):
        branch = parts[3]
        sub_path = '/'.join(parts[4:]) if len(parts) > 4 else ''
    else:
        branch = 'main'
        sub_path = ''

    _github_dir(owner, repo, branch, sub_path, dest / owner / repo,
                depth, 0, session, ext_filter or [], log=log)


def _github_dir(owner, repo, branch, path, dest, max_depth, current_depth, session, ext_filter,
                log: callable | None = None):
    _log = log or print
    if current_depth > max_depth:
        return

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    resp = None
    for br in [branch, 'main', 'master']:
        resp = session.get(api_url, params={'ref': br}, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200:
            break
        if resp.status_code != 404:
            _log(f"  ✗ GitHub API {resp.status_code}")
            return

    if resp is None or resp.status_code != 200:
        _log(f"  ✗ Repo inaccessible")
        return

    entries = resp.json()
    if not isinstance(entries, list):
        entries = [entries]

    for entry in entries:
        if entry.get('type') == 'file' and entry.get('download_url'):
            name = entry.get('name', 'file')
            if ext_filter and not any(name.endswith(e) for e in ext_filter):
                continue
            out = dest / _safe(name)
            dest.mkdir(parents=True, exist_ok=True)
            if out.exists():
                _log(f"  [skip] {name}")
                continue
            try:
                r = session.get(entry['download_url'], headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                out.write_bytes(r.content)
                _log(f"  ✅ {name}")
                time.sleep(0.2)
            except Exception as e:
                _log(f"  ✗ {name}: {e}")

        elif entry.get('type') == 'dir' and current_depth < max_depth:
            _log(f"  [dir] {entry['name']}/")
            _github_dir(owner, repo, branch, entry['path'],
                        dest / _safe(entry['name']),
                        max_depth, current_depth + 1, session, ext_filter, log=log)
