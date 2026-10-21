"""Bounded local diagnostics for server errors and browser artwork events."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit, urlunsplit


LOG_LOCK = threading.Lock()
LOG_BYTES = 5 * 1024 * 1024
EVENTS = {'card_removed', 'image_load_failed', 'image_load_recovered', 'video_retry', 'video_failed'}


def redact_sensitive(message: str) -> str:
    """Best-effort defense against credentials echoed by upstream error pages."""
    message = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', r'\1 [redacted]', message)
    keys = r'access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization|api[_-]?key|password'
    message = re.sub(r'''(?i)(\b(?:''' + keys + r''')\b["']?\s*[:=]\s*)(["'])(?:\\.|(?!\2)[^\r\n])*\2''',
                     r'\1\2[redacted]\2', message)
    message = re.sub(r'''(?i)(\b(?:''' + keys + r''')\b["']?\s*[:=]\s*)(?!["'])([^\s,}\]"']+)''',
                     r'\1[redacted]', message)
    message = re.sub(r'(?i)(https?://)[^/\s:@]+:[^/@\s]+@', r'\1[redacted]@', message)
    return re.sub(r'''(?i)([?&](?:token|access_token|refresh_token|api_key|code)=)[^&#\s"']+''',
                  r'\1[redacted]', message)


def append_log(root: Path, message: str) -> None:
    message = redact_sensitive(message)
    try:
        with LOG_LOCK:
            root.mkdir(parents=True, exist_ok=True)
            path = root / 'error.log'
            if path.exists() and path.stat().st_size + len(message.encode('utf-8')) > LOG_BYTES:
                path.replace(root / 'error.log.1')
            with path.open('a', encoding='utf-8') as output:
                output.write(message)
    except OSError:
        pass


def log_browser_events(root: Path, events: object) -> None:
    if not isinstance(events, list) or not 1 <= len(events) <= 10:
        raise ValueError('Provide 1 to 10 diagnostic events')
    records = []
    for event in events:
        if not isinstance(event, dict) or event.get('event') not in EVENTS:
            raise ValueError('Unknown diagnostic event')
        record = {'event': event['event']}
        for name, limit in (('reason', 100), ('username', 80), ('date', 10),
                            ('segment', 10), ('surface', 30)):
            if isinstance(event.get(name), str):
                record[name] = event[name][:limit]
        image_id = event.get('imageId')
        if str(image_id).isdigit() and 0 < int(image_id) < 2 ** 63:
            record['imageId'] = int(image_id)
        try:
            url = urlsplit(str(event.get('url') or ''))
            if url.scheme == 'https' and url.hostname == 'image.civitai.com':
                record['url'] = urlunsplit(('https', url.hostname, url.path[:400], '', ''))
        except ValueError:
            pass
        stamp = datetime.now(timezone.utc).isoformat()
        records.append(f'[{stamp}] Browser diagnostic: {json.dumps(record, ensure_ascii=False)}\n')
    append_log(root, ''.join(records))
