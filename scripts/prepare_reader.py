"""Fetch the pinned reader archive; never import it or access WeChat."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'wechatauto_replica-1.2.5.1-py3-none-any.whl'
SHA256 = '8f3de71dd9dc885b0b60d0d49fefaed857f44820dca8793b35e34f4c68896234'


def main():
    directory = ROOT / 'data/dependency-review'
    wheel = directory / NAME
    if wheel.exists():
        content = wheel.read_bytes()
    else:
        with urllib.request.urlopen('https://pypi.org/pypi/wechatauto-replica/1.2.5.1/json', timeout=30) as response:
            metadata = json.load(response)
        entry = next(item for item in metadata['urls'] if item['filename'] == NAME)
        if entry['digests']['sha256'] != SHA256 or not entry['url'].startswith('https://files.pythonhosted.org/'):
            raise RuntimeError('Unexpected upstream artifact; nothing was installed.')
        with urllib.request.urlopen(entry['url'], timeout=60) as response:
            content = response.read(20_000_001)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise RuntimeError('Reader checksum mismatch; nothing was imported.')
    directory.mkdir(parents=True, exist_ok=True)
    if not wheel.exists():
        wheel.write_bytes(content)
    with zipfile.ZipFile(wheel) as archive:
        for name in ('wechatauto/db.py', 'wechatauto/media.py',
                     'wechatauto_replica-1.2.5.1.dist-info/licenses/LICENSE'):
            target = directory / 'replica-1.2.5.1' / name
            source = archive.read(name)
            if target.exists() and target.read_bytes() != source:
                raise RuntimeError(f'Existing reviewed source differs: {name}')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source)
    print('Pinned reader prepared and verified. No WeChat access or package installation.')


if __name__ == '__main__':
    main()
