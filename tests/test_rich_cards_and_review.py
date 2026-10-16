from contact_fixtures import apply_enabled_snapshot
import json
import httpx
import pytest
from galchat.attachments import parse_message, readiness
from galchat.engine import ReplyEngine, ReplyEngineError, _parse_choices
from galchat.conversation_state import own_closing
from galchat.reply_review import parse_review
from galchat.engine import _check_unknown_own_actions
from galchat.controller import Controller
from pathlib import Path
from types import SimpleNamespace


def app(subtype, body=''):
    return f'<msg><appmsg><type>{subtype}</type><title>卡片标题</title>{body}</appmsg></msg>'


@pytest.mark.parametrize('subtype,kind', [('33','mini_program'),('36','mini_program'),('51','channel'),('63','live'),('17','live_location'),('62','pat'),('3','music'),('87','announcement'),('53','solitaire'),('130','file'),('4','application_card'),('999','application_card')])
def test_observed_card_kinds_allow_metadata_route_without_claiming_body(subtype,kind):
    parsed=parse_message((int(subtype)<<32)|49,'group_sender:\n'+app(subtype,'<aeskey>DO-NOT-SEND</aeskey>'))
    assert parsed['kind']==kind and not parsed['content_known']
    assert readiness([{**parsed,'role':'other'}]) is None
    payload=ReplyEngine._payload_context({'recent':[{**parsed,'id':'card','role':'other'}]})
    assert 'DO-NOT-SEND' not in json.dumps(payload)
    assert payload['recent'][0]['attachment']['scope']=='message_metadata'


def test_nested_type_never_overrides_app_subtype_or_title():
    parsed=parse_message(49,'<msg><appmsg><refermsg><type>19</type><title>引用</title></refermsg><type>33</type><title>小程序</title></appmsg></msg>')
    assert parsed['kind']=='mini_program' and parsed['attachment']['title']=='小程序'
    conflict=parse_message((33<<32)|49,app('36'))
    assert conflict['kind']=='other' and not conflict['content_known']


def test_openim_wrapper_is_data_not_a_new_speaker():
    parsed=parse_message((33<<32)|49,'12345678901234567@openim:\n'+app('33'))
    assert parsed['kind']=='mini_program'
    assert '@openim' not in json.dumps(parsed)


def test_location_pin_has_bounded_coordinates_not_claimed_live_location():
    parsed=parse_message(48,'<msg><location x="31.2" y="121.5" poiname="示例广场" label="示例路" fromusername="SECRET"/></msg>')
    assert parsed['attachment']['latitude']==31.2
    assert parsed['content_scope']=='metadata_only' and not parsed['content_known']
    assert 'SECRET' not in json.dumps(parsed)
    assert 'latitude' not in parse_message(48,'<location x="nan" y="999"/>')['attachment']


def test_note_keeps_original_authors_and_missing_attachments():
    data=parse_message(49,app('24','<recorditem><![CDATA[<recordinfo><datalist count="2"><dataitem datatype="1"><sourcename>原作者</sourcename><datadesc>去图书馆</datadesc></dataitem><dataitem datatype="2"><cdn_dataurl>SECRET</cdn_dataurl></dataitem></datalist></recordinfo>]]></recorditem>'))
    assert data['kind']=='note' and data['attachment']['status']=='partial'
    assert data['attachment']['missing_count']==1 and data['attachment']['items'][0]['author']=='原作者'
    assert 'SECRET' not in json.dumps(data)


def test_system_events_pass_separately_from_speakers_without_raw_xml():
    event=parse_message(10000,'<sysmsg type="revokemsg"><revokemsg><content>撤回了一条消息</content><revoketime>123</revoketime><secret>NEVER</secret></revokemsg></sysmsg>')
    ctx=ReplyEngine._payload_context({'recent':[{'id':'one','role':'other','text':'刚才说错了'}, {'id':'event','role':'system',**event}, {'id':'new','role':'other','text':'明天去'}]})
    assert [m['id'] for m in ctx['recent']]==['one','new']
    assert ctx['events'][0]['speaker_id']=='system' and ctx['events'][0]['attachment']['event']=='revokemsg'
    assert 'NEVER' not in json.dumps(ctx)
    assert len(ctx['timeline']['messages'])==3


def choice(text='晚安～'):
    return {'label':'轻松收尾','text':text,'hint':'可以不补发','tone':'warm'}


@pytest.mark.parametrize('state,count',[('reply',3),('close',1),('wait',0)])
def test_count_contract(state,count):
    choices=[{**choice(str(i)), 'label':str(i)} for i in range(count)]
    assert len(_parse_choices(json.dumps({'conversation_state':state,'choices':choices})))==count
    with pytest.raises(ReplyEngineError):
        _parse_choices(json.dumps({'conversation_state':state,'choices':choices+[choice('多余')]}))


def test_own_explicit_closing_waits_without_api_or_fill_authority():
    with httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('must not call model'))) as client:
        result=ReplyEngine(client).generate({'recent':[{'id':'own','role':'self','text':'晚安～','timestamp':'2020-01-01'}]}, {'base_url':'https://api.test','model':'test','verify_replies':True})
    assert result['choices']==[] and result['conversation_state']=='wait'
    assert result['retained_closing']['message_id']=='own'
    assert 'batch_id' not in result['retained_closing']


def test_reciprocal_goodnight_retains_actual_own_closing_but_new_topic_reopens():
    ctx={'recent':[{'role':'other','text':'晚安'},{'id':'own','role':'self','text':'晚安～','timestamp':'2026-10-13T23:00:00'},
                   {'role':'other','text':'晚安','timestamp':'2026-10-13T23:00:02'}]}
    assert own_closing(ctx)['message_id']=='own'
    ctx['recent'][-1]['text']='晚安，还有个问题'
    assert own_closing(ctx) is None
    assert own_closing({'recent':[{'role':'self','text':'好，你先忙'}]})['text']=='好，你先忙'


def test_week_later_goodnight_is_not_fast_closed_as_a_reciprocal_ack():
    ctx={'recent':[{'id':'own','role':'self','text':'晚安','timestamp':'2026-10-01T23:00:00'},
                   {'role':'other','text':'晚安','timestamp':'2026-10-08T23:00:00'}]}
    assert own_closing(ctx) is None


@pytest.mark.parametrize('text',['晚安，还有个问题','我明天有空吗？','好','嗯','晚安，明天你几点来？'])
def test_time_gaps_or_closing_words_inside_questions_never_trigger_fast_wait(text):
    assert own_closing({'recent':[{'role':'self','text':text,'timestamp':'2020-01-01'}]}) is None


def test_reviewer_checks_identity_citations_and_complete_coverage():
    ctx=ReplyEngine._payload_context({'recent':[{'id':'peer','role':'other','text':'我家猫打翻杯子了'}]})
    choices=[{'id':'choice-1',**choice('这只猫还挺忙')}]
    review={'conversation_state':'close','checks':[{'choice_id':'choice-1','decision':'pass','reason':'反应','claims':[],'evidence':[{'message_id':'peer','speaker_id':'chat_peer','quote':'我家猫'}]}]}
    assert parse_review(json.dumps(review),choices,ctx)==('close',[])
    review['checks'][0]['evidence'][0]['speaker_id']='wechat_user'
    with pytest.raises(ValueError,match='发言人'):parse_review(json.dumps(review),choices,ctx)
    review['checks']=[]
    with pytest.raises(ValueError,match='全部'):parse_review(json.dumps(review),choices,ctx)


def test_failed_semantic_review_gets_one_repair_and_then_blocks_choices():
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        if '事实审稿人' in body['messages'][0]['content']:
            output={'conversation_state':'close','checks':[{'choice_id':'choice-1','decision':'fail','reason':'虚构经历','evidence':[]}]}
        else:output={'conversation_state':'close','choices':[choice('我也养过这只猫')]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output)}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError,match='事实核对未通过'):
            ReplyEngine(client).generate({'recent':[{'role':'other','id':'p','text':'我家猫打翻杯子了'}]}, {'base_url':'https://api.test','model':'test','verify_replies':True})
    assert len(calls)==4
    assert '虚构经历' in calls[2]['messages'][-1]['content']


@pytest.mark.parametrize('text',['我还没问过','我也在纠结','周六不用加班','我也不清楚'])
def test_missing_information_never_becomes_an_own_negative_or_mental_fact(text):
    context={'recent':[{'id':'own','role':'self','text':'我想给妹妹买个礼物','content_known':True}]}
    with pytest.raises(ReplyEngineError):_check_unknown_own_actions([{'text':text}],context)
    _check_unknown_own_actions([{'text':text}],context,intent=text)


def test_refresh_native_voice_preserves_speaker_time_and_needs_no_asr_key(tmp_path):
    root=Path(__file__).resolve().parents[1]
    controller=Controller(root,tmp_path,start_worker=False)
    voice={'id':'voice','role':'other','kind':'voice','text':'[语音]','content_known':False,'timestamp':'2026-10-01T10:00:00'}
    original={'contact':{'id':'a','name':'虚构甲'},'messages':[voice]}
    apply_enabled_snapshot(controller, original)
    fresh={**original,'messages':[{**voice,'text':'明天下午去图书馆','content_known':True,'content_source':'wechat_transcription'}]}
    controller.current_adapter=SimpleNamespace(snapshot=lambda:fresh,close=lambda:None)
    try:
        result=controller._handle_native('refresh_voice_text',{'contact_id':'a','revision':controller.revision,'id':'voice'})
        actual=controller.state['messages'][0]
        assert '已读取微信转文字' in result['message']
        assert actual['role']=='other' and actual['timestamp']==voice['timestamp'] and actual['id']=='voice'
        assert not controller.settings.asr_config().get('api_key')
        with pytest.raises(ValueError):controller._handle_native('refresh_voice_text',{'contact_id':'b','revision':controller.revision,'id':'voice'})
    finally:controller.close()


def test_review_quote_format_gets_one_retry_without_regenerating_valid_choices():
    requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:output={'conversation_state':'close','choices':[choice()]}
        else:output={'conversation_state':'close','checks':[{'choice_id':'choice-1','decision':'pass','reason':'收尾','claims':[],
            'evidence':[{'message_id':'peer','speaker_id':'chat_peer','quote':'不存在的引文' if len(requests)==2 else '晚安'}]}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output)}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=ReplyEngine(client).generate({'recent':[{'id':'peer','role':'other','text':'晚安'}]}, {'base_url':'https://api.test','model':'test','verify_replies':True})
    assert len(requests)==3 and len(result['choices'])==1
    assert all('事实审稿人' in r['messages'][0]['content'] for r in requests[1:])
    assert '原文证据不符' in requests[2]['messages'][-1]['content']
