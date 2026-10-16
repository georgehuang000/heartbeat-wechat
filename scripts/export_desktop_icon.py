"""Export the existing brand artwork into ICO frames with desktop-size padding.

The original PNG remains unchanged. Near-transparent fringe is ignored when
measuring the artwork; its actual pixels are preserved within the crop.
"""
import hashlib
import json
import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 128, 256)


def export():
    source = ROOT / 'prototype-heart-wechat/public/assets/app-icon.png'
    directory = ROOT / 'assets'
    directory.mkdir(exist_ok=True)
    target = directory / 'heart-wechat.ico'
    backup = directory / 'heart-wechat-padded-v1.ico'
    with Image.open(source) as original:
        image = original.convert('RGBA')
    if not backup.exists():
        image.save(backup, format='ICO', sizes=[(n, n) for n in SIZES])
    # The generated source has faint alpha=1 pixels far outside the rounded
    # square. getbbox() without a threshold would retain the excess padding.
    bounds = image.getchannel('A').point(lambda v: 255 if v >= 8 else 0).getbbox()
    if bounds is None:
        raise ValueError('The brand icon is empty')
    left, top, right, bottom = bounds
    side = math.ceil(max(right-left, bottom-top) / .96)
    x = round((left+right-side) / 2)
    y = round((top+bottom-side) / 2)
    crop = (x, y, x+side, y+side)
    if x < 0 or y < 0 or crop[2] > image.width or crop[3] > image.height:
        raise ValueError('Desktop icon crop exceeds the original canvas')
    exported = image.crop(crop)
    png = directory / 'heart-wechat-desktop-v2.png'
    exported.save(png)
    exported.save(target, format='ICO', sizes=[(n, n) for n in SIZES])
    report = {
        'source': str(source.relative_to(ROOT)),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'desktop_png': str(png.relative_to(ROOT)),
        'ico': str(target.relative_to(ROOT)), 'source_size': image.size,
        'significant_alpha_threshold': 8, 'artwork_bounds': bounds,
        'crop': crop, 'export_size': exported.size,
        'previous_artwork_width_ratio': (right-left)/image.width,
        'desktop_artwork_width_ratio': (right-left)/side,
        'visual_scale_change': image.width/side,
        'source_preserved': True,
    }
    samples = []
    for size in (16, 32, 48, 64):
        widths = {}
        for label, path in (('previous', backup), ('updated', target)):
            frame = Image.open(path).ico.getimage((size, size))
            box = frame.getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox()
            widths[label] = box[2]-box[0]
        samples.append({'canvas_px': size, 'visible_width_px': widths})
    report['frame_comparison'] = samples
    (directory / 'desktop-icon-export.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(export(), ensure_ascii=False))
