"""Independent automatic quotas; missing pixels never become known content."""


def automatic_visuals(messages, settings):
    if settings.get('media_policy') == 'off':
        return []
    photos = [m for m in messages if m.get('role') in {'self', 'other'}
              and m.get('kind') == 'image']
    stickers = [m for m in messages if m.get('role') in {'self', 'other'}
                and m.get('kind') == 'emoji' and m.get('has_sticker')]
    photo_limit = settings.get('image_limit', 3) if settings.get('auto_images', True) else 0
    sticker_limit = settings.get('sticker_limit', 0)
    ids = {m['id'] for m in (photos[-photo_limit:] if photo_limit else [])
           + (stickers[-sticker_limit:] if sticker_limit else [])}
    return [m for m in messages if m.get('id') in ids and not m.get('content_known')]


def visual_enrichment_only(before, after):
    if not before or len(before) != len(after):
        return False
    def stable(message):
        return {k: message.get(k) for k in ('id', 'role', 'kind', 'text', 'timestamp',
            'content_known', 'content_source', 'attachment', 'participant_id', 'sender_display_name')}
    return all(stable(a) == stable(b) for a, b in zip(before, after))
