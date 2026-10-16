"""Display only the current chat's exact sticker from local WeChat caches.

Cache layout observed on Windows WeChat 4.1. AES modes cross-checked against
Ant39140/wechat_meme_export; no CDN request, arbitrary file path or saved key.
"""
import base64
import hashlib
import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit

from PIL import Image

MAX_BYTES = 3_000_000


def sticker_reference(content):
    if not isinstance(content, str) or len(content) > 65536 or '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
        return None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    emoji = root if root.tag == 'emoji' else root.find('.//emoji')
    if emoji is None:
        return None
    md5 = emoji.get('md5', '').lower()
    if not re.fullmatch('[0-9a-f]{32}', md5):
        return None
    key = emoji.get('aeskey', '')
    return {'md5': md5, 'aeskey': key if re.fullmatch('[0-9a-fA-F]{32}', key) else '',
            'urls': [url for url in (emoji.get('cdnurl', ''), emoji.get('encrypturl', '')) if _cdn_url(url)]}


def _cdn_url(url):
    if not isinstance(url, str) or len(url) > 8192:
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {'http', 'https'} or parts.hostname not in
                {'wxapp.tc.qq.com', 'vweixinf.tc.qq.com', 'mmbiz.qpic.cn'}
                or parts.username or parts.password or parts.port not in (None, 80, 443)):
            return None
    except ValueError:
        return None
    # Fetch only official exact hosts; do not follow redirects to arbitrary URLs.
    return urlunsplit(('https', parts.netloc, parts.path, parts.query, ''))


def _validated_image(data, md5):
    if not data or len(data) > MAX_BYTES or hashlib.md5(data).hexdigest() != md5:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG', 'GIF', 'WEBP'} or image.width * image.height > 16_000_000:
                return None
            mime = Image.MIME[image.format]
            image.verify()
    except (ValueError, OSError, Image.DecompressionBombError):
        return None
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')


def _decode(data, reference):
    result = _validated_image(data, reference['md5'])
    if result:
        return result
    if not reference['aeskey'] or len(data) % 16:
        return None
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = bytes.fromhex(reference['aeskey'])
    for mode in (modes.CBC(bytes(16)), modes.ECB()):
        decryptor = Cipher(algorithms.AES(key), mode).decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
        candidates = [plain]
        pad = plain[-1] if plain else 0
        if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
            candidates.append(plain[:-pad])
        for candidate in candidates:
            result = _validated_image(candidate, reference['md5'])
            if result:
                return result
    return None


def local_sticker(account_dir, reference):
    base = Path(account_dir).resolve()
    md5 = reference['md5']
    if not re.fullmatch('[0-9a-f]{32}', md5):
        return None
    roots = [base / 'business' / 'emoticon' / 'Persist']
    cache = base / 'cache'
    if cache.is_dir():
        roots.extend(p / 'Emoticon' for p in sorted(cache.iterdir(), reverse=True)[:36]
                     if re.fullmatch(r'\d{4}-\d{2}', p.name))
    for root in roots:
        path = (root / md5[:2] / md5).resolve()
        if not path.is_relative_to(base):
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
            result = _decode(data, reference)
            if result:
                return result
        except (OSError, ValueError):
            continue
    return None


def read_sticker(account_dir, reference, cache_dir, client=None):
    result = local_sticker(account_dir, reference)
    if result:
        return result
    cache = Path(cache_dir) / reference['md5']
    if cache.is_file() and cache.stat().st_size <= MAX_BYTES:
        result = _validated_image(cache.read_bytes(), reference['md5'])
        if result:
            return result
    import httpx
    def fetch(client):
        for original in reference.get('urls', [])[:2]:
            url = _cdn_url(original)
            if not url:
                continue
            try:
                with client.stream('GET', url, timeout=8, follow_redirects=False) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            break
                    if len(data) > MAX_BYTES:
                        continue
                result = _decode(bytes(data), reference)
                if result:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(base64.b64decode(result.split(',', 1)[1]))
                    return result
            except (httpx.HTTPError, OSError, ValueError):
                continue
        return None
    if client is not None:
        return fetch(client)
    with httpx.Client(trust_env=False) as client:
        return fetch(client)
