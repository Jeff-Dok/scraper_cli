import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

MIN_SIZE_VIDEO_AUDIO = 65536   # 64 KB
MIN_SIZE_DOC_ARCHIVE = 1024    # 1 KB

SESSIONS_DIR = Path(__file__).parent.parent / "sessions"


def is_partial(path: Path, content_length: int | None, min_size: int = MIN_SIZE_DOC_ARCHIVE) -> bool:
    if not path.exists():
        return False
    size = path.stat().st_size
    if content_length is not None:
        return size != content_length
    return size < min_size


def load_session(url: str, sessions_dir: Path = SESSIONS_DIR) -> dict | None:
    if not sessions_dir.exists():
        return None
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            if data.get('url') == url:
                data['_session_file'] = str(f)
                return data
        except (json.JSONDecodeError, OSError):
            continue
    return None


def save_session(data: dict, sessions_dir: Path = SESSIONS_DIR) -> None:
    """Persist session state to a JSON file.

    Side effect: adds '_session_file' key to `data` if not already present,
    so the same dict can be passed again to update in-place without creating duplicates.
    """
    url = data.get('url')
    if not url:
        raise ValueError("session data must contain a 'url' key")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if '_session_file' in data:
        path = Path(data['_session_file'])
    else:
        netloc = urlparse(url).netloc.replace('.', '_').replace(':', '_')
        ts = data.get('started_at', datetime.now().isoformat(timespec='seconds'))
        ts_clean = ts.replace(':', '').replace('-', '').replace('T', '')[:14]
        path = sessions_dir / f"{ts_clean}_{netloc}.json"
        data['_session_file'] = str(path)  # kept in memory only; excluded from disk below
    to_save = {k: v for k, v in data.items() if k != '_session_file'}
    path.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding='utf-8')
