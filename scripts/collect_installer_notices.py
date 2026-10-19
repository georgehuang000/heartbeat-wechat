"""Collect installed dependency license texts for this local release."""
from importlib import metadata
from pathlib import Path
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
names = ['pywebview', 'uiautomation', 'pywin32', 'httpx', 'fastapi', 'uvicorn',
         'pysilk-mod', 'pythonnet', 'clr_loader', 'cryptography', 'zstandard',
         'comtypes', 'bottle', 'proxy_tools', 'typing_extensions', 'pydantic',
         'pydantic_core', 'starlette', 'anyio', 'sniffio', 'httpcore', 'h11',
         'certifi', 'idna', 'click', 'cffi', 'pycparser', 'typing_inspection',
         'annotated-types', 'PyInstaller']
parts = ['心动微信 0.1.1 · Third-party notices',
         'This file collects dependency metadata and available license texts from the build environment.\n'
         'It does not replace the individual upstream terms. The app package does not include user data.\n'
         "The standard package includes Microsoft's unmodified, signed WebView2 Evergreen Bootstrapper.\n"
         "When the Runtime is missing, the Bootstrapper downloads it directly from Microsoft during setup.\n"
         "The offline package includes Microsoft's signed Evergreen Standalone Installer (x64) instead.\n"
         'The Runtime is provided by Microsoft under its own terms and is installed only when missing.\n'
         'https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution\n'
         'https://developer.microsoft.com/microsoft-edge/webview2\n'
         'Inno Setup: https://github.com/jrsoftware/issrc/tree/is-6_7_3\n'
         'Chinese translation: Files/Languages/Unofficial/ChineseSimplified.isl, same tag.']
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    parts += ['Python ' + sys.version, python_license.read_text('utf-8', errors='replace')]
for name in names:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        continue
    parts += ['\n' + '=' * 72, name + ' ' + dist.version,
              'License metadata: ' + (dist.metadata.get('License-Expression') or dist.metadata.get('License') or '(not specified)'),
              'Home: ' + (dist.metadata.get('Home-page') or '; '.join(dist.metadata.get_all('Project-URL') or []))]
    for file in dist.files or []:
        if file.name.lower().startswith(('license', 'licence', 'copying', 'notice')):
            path = Path(dist.locate_file(file))
            if path.is_file() and path.stat().st_size < 500_000:
                parts += ['\n--- ' + str(file) + ' ---', path.read_text('utf-8', errors='replace')]
wheel = root / 'data/dependency-review/wechatauto_replica-1.2.5.1-py3-none-any.whl'
with zipfile.ZipFile(wheel) as archive:
    parts += ['\n' + '=' * 72, 'Reviewed dependency archive: ' + wheel.name]
    licenses = [n for n in archive.namelist() if Path(n).name.lower().startswith(('license', 'copying', 'notice'))]
    if licenses:
        for n in licenses:
            parts += [n, archive.read(n).decode('utf-8', errors='replace')]
    else:
        parts += ['No standalone license file was present in this reviewed archive. External redistribution rights require separate confirmation.']
inno_license = root / 'build/installer-tools/InnoSetup/License.txt'
if inno_license.exists():
    parts += ['\n' + '=' * 72, 'Inno Setup license', inno_license.read_text('utf-8', errors='replace')]
target = root / 'packaging/THIRD-PARTY-NOTICES.txt'
target.write_text('\n\n'.join(parts).replace('\r\n', '\n'), 'utf-8', newline='\n')
print(f'Collected {len(names)} distribution candidates into {target.name} ({target.stat().st_size} bytes).')
