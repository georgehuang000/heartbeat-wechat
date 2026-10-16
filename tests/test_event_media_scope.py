import base64
import io
import json

from PIL import Image
import pytest

from galchat.attachments import parse_message, readiness
from galchat.engine import ReplyEngine
from galchat.multimodal import visual_parts


def animation():
    out=io.BytesIO()
    frames=[Image.new('RGB',(12,12),color) for color in ('red','blue')]
    frames[0].save(out,format='GIF',save_all=True,append_images=frames[1:],duration=500)
    return 'data:image/gif;base64,'+base64.b64encode(out.getvalue()).decode()


@pytest.mark.parametrize('role,speaker',[('self','wechat_user'),('other','chat_peer')])
def test_animation_only_sends_frame_zero_and_keeps_sender_time(role,speaker):
    original=animation()
    context={'contact_id':'c','recent':[{'id':'m','role':role,'kind':'emoji','text':'[表情]',
             'timestamp':'2026-10-13T18:00:00','content_known':False}],
             'visual_inputs':[{'contact_id':'c','message_id':'m','tier':'sticker','data_url':original}]}
    payload=ReplyEngine._payload_context(context)
    parts=visual_parts(context,payload)
    header=json.loads(parts[0]['text'])
    assert header['speaker_id']==speaker and header['timestamp']=='2026-10-13T18:00:00'
    assert header['content_scope']=='first_frame_only'
    url=parts[1]['image_url']['url']
    assert url.startswith('data:image/png;')
    with Image.open(io.BytesIO(base64.b64decode(url.partition(',')[2]))) as picture:
        assert picture.n_frames==1 and picture.convert('RGB').getpixel((0,0))==(255,0,0)
    assert payload['recent'][0]['visual_input']['frame_index']==0
    assert context['visual_inputs'][0]['data_url']==original


@pytest.mark.parametrize('code',[43,62])
def test_video_is_a_readable_event_with_unread_content_not_a_generation_block(code):
    row=parse_message(code,'<msg><videomsg cdnvideourl="private-url"/></msg>')
    assert row['kind']=='video' and row['content_known'] is False
    assert row['content_scope']=='event_only'
    assert row['attachment']['status']=='video_event_only'
    assert readiness([{**row,'role':'other'}]) is None
    payload=ReplyEngine._payload_context({'recent':[{**row,'role':'other','id':'video'}]})
    assert payload['recent'][0]['attachment']['event']=='video_message'
    assert 'private-url' not in json.dumps(payload)


def test_call_only_passes_event_and_no_claimed_audio_content():
    row=parse_message(50,'<voipmsg><msg>已取消</msg><audiourl>private-url</audiourl></voipmsg>')
    assert '已取消' in row['text'] and row['attachment']['status']=='call_event_only'
    assert 'private-url' not in json.dumps(row)

