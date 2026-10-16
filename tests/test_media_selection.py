import pytest
from galchat.media_selection import automatic_visuals
from galchat.settings import Settings


def test_photo_and_sticker_quotas_are_separate_and_keep_both_senders():
    rows=[{'id':str(i),'kind':'image' if i%2==0 else 'emoji','has_sticker':True,
           'role':'self' if i<3 else 'other','content_known':False} for i in range(8)]
    assert [m['id'] for m in automatic_visuals(rows,{'image_limit':3,'sticker_limit':0})]==['2','4','6']
    assert [m['id'] for m in automatic_visuals(rows,{'image_limit':1,'sticker_limit':1})]==['6','7']
    assert automatic_visuals(rows,{'image_limit':5,'sticker_limit':1,'media_policy':'off'})==[]


@pytest.mark.parametrize('field,value',[('image_limit',2),('sticker_limit',3),('reply_hold_seconds',0),('image_limit',True)])
def test_invalid_media_and_hold_controls_cannot_partially_save(tmp_path,field,value):
    settings=Settings(tmp_path)
    before=dict(settings.values)
    with pytest.raises(ValueError):settings.save({field:value,'auto_generate':False})
    assert settings.values==before
