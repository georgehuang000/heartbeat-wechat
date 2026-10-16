import base64
import io
import json

import httpx
import pytest
from PIL import Image

from galchat.attachments import parse_message, readiness
from galchat.engine import ReplyEngine
from galchat.media import data_url


def test_animation_transport_is_exactly_frame_zero_in_actual_reply_request():
    original=io.BytesIO()
    Image.new('RGB',(40,40),'red').save(original,format='GIF',save_all=True,
        append_images=[Image.new('RGB',(40,40),'blue')],duration=100,loop=0)
    url=data_url(original.getvalue())
    context={'contact_id':'c','recent':[{'id':'s','role':'other','kind':'emoji','text':'[表情]'}],
             'visual_inputs':[{'contact_id':'c','message_id':'s','tier':'sticker','data_url':url}]}
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'choices':[
            {'label':f'方向{i}','text':f'第{i}种可选说法','hint':'首帧','tone':'calm'} for i in range(3)]})}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(context,{'base_url':'https://example.test','model':'test'})
    parts=captured[0]['messages'][1]['content']
    payload=json.loads(parts[0]['text'])['context']['recent'][0]
    assert payload['visual_input']['content_scope']=='first_frame_only'
    assert payload['speaker_id']=='chat_peer'
    assert json.loads(parts[1]['text'])['content_scope']=='first_frame_only'
    raw=base64.b64decode(parts[2]['image_url']['url'].split(',',1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format=='PNG' and getattr(image,'n_frames',1)==1
        assert image.convert('RGB').getpixel((20,20))==(255,0,0)
    assert context['visual_inputs'][0]['data_url']==url


@pytest.mark.parametrize('code',[43,62])
def test_video_only_contributes_an_event_not_assumed_readable_content(code):
    row=parse_message(code,'<msg><videomsg/></msg>')
    assert row['kind']=='video' and not row['content_known']
    assert row['attachment']['status']=='video_event_only'
    assert readiness([{**row,'role':'other'}]) is None
    payload=ReplyEngine._payload_context({'recent':[{'id':'v','role':'self',**row}]})
    assert payload['recent'][0]['attachment']['event']=='video_message'
    assert payload['recent'][0]['speaker_id']=='wechat_user'
    assert 'video_url' not in json.dumps(payload)


def test_duplicate_payment_type_does_not_select_a_status():
    row=parse_message((2000<<32)|49,'<msg><appmsg><type>2000</type><type>2001</type><wcpayinfo><paysubtype>3</paysubtype></wcpayinfo></appmsg></msg>')
    assert not row['content_known'] and row['attachment']['event']=='payment_status_unknown'

