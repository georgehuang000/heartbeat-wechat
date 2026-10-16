"""Own packed transcript + own pixels through Controller to HTTP model body.

Synthetic DB/resources and mocked provider; never accesses WeChat or the net.
"""
from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import base64
import io
import json
import time
import httpx
from PIL import Image
from galchat.contact_database import ContactDatabase
from galchat.controller import Controller
from galchat.engine import ReplyEngine

ROOT=Path(__file__).resolve().parents[1]


def test_own_native_voice_and_own_image_reach_generation_and_review_with_sender_time(tmp_path):
    transcript='我把候选图片发给你，想听听你的意见。'
    raw_text=transcript.encode('utf-8')
    nested=b'\x08\x02\x12'+bytes([len(raw_text)])+raw_text
    packed=b'\x2a'+bytes([len(nested)])+nested
    class Reader:
        wxid='fixture-self'
        def search_contact(self,name):return [{'username':'fixture-peer','nick_name':name}]
        def get_messages(self,*args,**kwargs):
            common={'_galchat_shard':'fixture','create_time':1760314800}
            return [
                {**common,'local_id':13,'sort_seq':13,'local_type':1,'sender_username':'fixture-peer','content':'我看看','create_time':1760314805},
                {**common,'local_id':12,'sort_seq':12,'local_type':3,'sender_username':self.wxid,'content':'<msg><img/></msg>','create_time':1760314802},
                {**common,'local_id':11,'sort_seq':11,'local_type':34,'sender_username':self.wxid,'content':'<msg><voicemsg voicelength="4100"/></msg>','_packed_info':packed}]
    db=ContactDatabase(ROOT,tmp_path,Reader())
    contact=db.resolve('虚构联系人')
    def snapshot():return {'contact':contact,'messages':db.recent(contact,100)}
    initial=snapshot()
    original=deepcopy(initial['messages'])
    stream=io.BytesIO();Image.new('RGB',(30,30),'blue').save(stream,format='PNG')
    pixels=stream.getvalue()
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        result={'conversation_state':'wait','choices':[]} if len(requests)==1 else {'conversation_state':'wait','checks':[]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result)}}]})
    app=Controller(ROOT,tmp_path,start_worker=False)
    app.database=db
    app.state['settings']=app.settings.save({'api_key':'fixture-key','media_policy':'ask'})
    apply_enabled_snapshot(app, initial)
    app.connected=True
    app.state['connection']={'status':'connected','can_read':True,'can_fill':True}
    app.current_adapter=NS(current=lambda:{'name':'虚构联系人'})
    app.adapter=NS(snapshot=snapshot)
    app.local_media=NS(image=lambda *args,**kwargs:{'bytes':pixels,'tier':'original'},
        voice=lambda *args:(_ for _ in ()).throw(AssertionError('must use stored text, no audio ASR')))
    try:
        app._maybe_prepare_image()
        operation,job,_=app.commands.get(timeout=2)
        app._handle_native(operation,job)
        assert app.state['generation_blocked_reason'] is None
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            app.engine=ReplyEngine(client)
            app.pending_at=0
            app._maybe_generate()
            deadline=time.monotonic()+3
            while app.generating and time.monotonic()<deadline:time.sleep(.01)
            assert not app.generating
            assert app.state['generation']['status']=='ready',app.state['generation']
        assert len(requests)==2  # Production generation and fact review.
        for body in requests:
            content=body['messages'][1]['content']
            context=json.loads(content[0]['text'])['context']
            own_voice,own_image,peer=context['recent']
            assert own_voice['text']==transcript and own_voice['content_source']=='wechat_transcription'
            assert own_voice['role']=='self' and own_voice['speaker_id']=='wechat_user'
            assert own_image['role']=='self' and own_image['speaker_id']=='wechat_user'
            assert peer['role']=='other' and peer['speaker_id']=='chat_peer'
            assert all(sent['id']==source['id'] and sent['timestamp']==source['timestamp']
                for sent,source in zip(context['recent'],original))
            header=json.loads(content[1]['text'])
            assert header['visual_message_id']==original[1]['id'] and header['chat_role']=='self'
            assert header['speaker_id']=='wechat_user' and header['timestamp']==original[1]['timestamp']
            assert base64.b64decode(content[2]['image_url']['url'].partition(',')[2])==pixels
            assert '[语音]' not in own_voice['text']
    finally:
        app.close();db.close()
