"""Provider wire contracts with fictional requests and responses; no API billing."""
import base64
from copy import deepcopy
import io
import json
import httpx
from PIL import Image
import pytest

from galchat.api_protocols import normalized_response, ProtocolError, usage_from_response
from galchat.api_usage import ApiUsageLog
from galchat.engine import ReplyEngine, ReplyEngineError
from galchat.settings import Settings
from galchat.vision import VisionReader

PROTOCOLS=('openai_chat','openai_responses','anthropic_messages')
CHOICES=[{'id':f'choice-{i}','text':text,'label':('提个建议','问问偏好','商量时间')[i-1],'hint':'提出可选方向','tone':'calm'}
    for i,text in enumerate(('要不要去公园走走？','你有想去的地方吗？','我们先商量时间吧'),1)]
CONTEXT={'contact_id':'A','contact_name':'虚构联系人','recent':[
    {'id':'r1','kind':'text','role':'other','text':'周末想出去走走','content_known':True,'timestamp':'2026-10-14T10:00:00'}]}


def response(protocol, content):
    if protocol=='openai_chat':
        return {'id':'fictional','model':'fixture','choices':[{'message':{'content':content},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}
    if protocol=='openai_responses':
        return {'id':'fictional','model':'fixture','status':'completed','output':[
            {'type':'reasoning','summary':[]},{'type':'message','role':'assistant','content':[{'type':'output_text','text':content}]}],
            'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120,'input_tokens_details':{'cached_tokens':30}}}
    return {'id':'fictional','model':'fixture','stop_reason':'end_turn','content':[
        {'type':'thinking','thinking':'ignored optional block'},{'type':'text','text':content}],
        'usage':{'input_tokens':60,'cache_creation_input_tokens':10,'cache_read_input_tokens':30,'output_tokens':20}}


@pytest.mark.parametrize('protocol',PROTOCOLS)
def test_reply_and_fact_review_both_use_selected_protocol_and_log_numeric_usage(protocol,tmp_path):
    calls=[];ledger=ApiUsageLog(tmp_path)
    def handler(request):
        calls.append(request)
        result={'conversation_state':'reply','choices':CHOICES} if len(calls)==1 else {
            'conversation_state':'reply','checks':[{'choice_id':c['id'],'decision':'pass',
                'reason':'明确提议','evidence':[],'claims':[]} for c in CHOICES]}
        return httpx.Response(200,json=response(protocol,json.dumps(result,ensure_ascii=False)))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=ReplyEngine(client,usage_observer=ledger.record).generate(CONTEXT,{
            'base_url':'https://provider.example.test','model':'fixture','api_key':'fictional-key',
            'api_type':protocol,'verify_replies':True})
    assert result['review']['status']=='passed' and len(result['choices'])==3 and len(calls)==2
    ending={'openai_chat':'chat/completions','openai_responses':'responses','anthropic_messages':'messages'}[protocol]
    assert all(str(r.url)=='https://provider.example.test/v1/'+ending for r in calls)
    for request in calls:
        if protocol=='anthropic_messages':
            assert request.headers['x-api-key']=='fictional-key' and 'authorization' not in request.headers
            assert request.headers['anthropic-version']=='2023-06-01'
            body=json.loads(request.content)
            assert 'system' in body and 'response_format' not in body and 'temperature' not in body
        else:assert request.headers['authorization']=='Bearer fictional-key'
    if protocol=='openai_responses':
        body=json.loads(calls[0].content)
        assert body['store'] is False and body['text']['format']['type']=='json_object'
        assert body['max_output_tokens']==900 and 'messages' not in body and 'temperature' not in body
    summary=ledger.summary()
    assert summary['requests']==2 and summary['total_tokens']==240 and summary['input_tokens']==200
    events=[json.loads(line) for line in ledger.path.read_text('utf-8').splitlines()]
    assert [e['operation'] for e in events]==['reply_generation','fact_review']
    assert all(e['api_type']==protocol for e in events)
    assert 'fictional-key' not in ledger.path.read_text('utf-8')


@pytest.mark.parametrize('protocol',PROTOCOLS)
def test_image_transport_preserves_actual_pixels_sender_timestamp_and_vision_json(protocol):
    out=io.BytesIO();Image.new('RGB',(3,3),'pink').save(out,format='PNG');picture=out.getvalue()
    url='data:image/png;base64,'+base64.b64encode(picture).decode()
    context=deepcopy(CONTEXT)
    context['recent'].insert(0,{'id':'photo','kind':'image','role':'self','text':'[图片]',
        'content_known':False,'timestamp':'2026-10-14T09:59:00'})
    context['visual_inputs']=[{'contact_id':'A','message_id':'photo','data_url':url,'tier':'original'}]
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        content=json.dumps({'choices':CHOICES}) if len(calls)==1 else '{"contact_name":"虚构标题"}'
        return httpx.Response(200,json=response(protocol,content))
    config={'base_url':'https://provider.example.test/v1','model':'fixture','api_type':protocol,'api_key':'fixture'}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(context,config)
        assert VisionReader(client).read_title(picture,config)['contact_name']=='虚构标题'
    parts=(calls[0]['messages'][1]['content'] if protocol=='openai_chat' else
           calls[0]['input'][0]['content'] if protocol=='openai_responses' else calls[0]['messages'][0]['content'])
    notice=json.loads(next(p['text'] for p in parts if p.get('type') in {'text','input_text'}
                          and 'visual_message_id' in p.get('text','') and 'context' not in p.get('text','')))
    assert notice['chat_role']=='self' and notice['visual_message_id']=='photo'
    assert notice['timestamp']=='2026-10-14T09:59:00'
    if protocol=='anthropic_messages':
        image=next(p for p in parts if p.get('type')=='image')['source']
        assert image['media_type']=='image/png' and base64.b64decode(image['data'])==picture
    elif protocol=='openai_responses':assert next(p for p in parts if p.get('type')=='input_image')['image_url']==url
    else:assert next(p for p in parts if p.get('type')=='image_url')['image_url']['url']==url


@pytest.mark.parametrize('protocol',PROTOCOLS)
def test_format_repair_preserves_messages_in_native_wire_contract(protocol):
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=response(protocol,'not JSON' if len(calls)==1 else json.dumps({'choices':CHOICES})))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert len(ReplyEngine(client).generate(CONTEXT,{'base_url':'https://fixture.test/v1',
            'model':'fixture','api_type':protocol})['choices'])==3
    rows=calls[1]['input'] if protocol=='openai_responses' else calls[1]['messages']
    assert rows[-2]['role']=='assistant' and rows[-2]['content']=='not JSON'
    assert rows[-1]['role']=='user' and '校验未通过' in rows[-1]['content']


@pytest.mark.parametrize('protocol',PROTOCOLS)
def test_model_list_auth_and_full_endpoint_do_not_duplicate_paths(protocol):
    suffix={'openai_chat':'chat/completions','openai_responses':'responses','anthropic_messages':'messages'}[protocol]
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(200,json={'data':[{'id':'fixture'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ReplyEngine(client).probe({'base_url':'https://fixture.test/proxy/v1/'+suffix,
            'model':'fixture','api_key':'fixture','api_type':protocol})['model_listed']
    assert str(calls[0].url)=='https://fixture.test/proxy/v1/models'


@pytest.mark.parametrize('protocol',('openai_responses','anthropic_messages'))
def test_wrong_wire_response_is_an_actionable_type_mismatch(protocol):
    with pytest.raises(ProtocolError,match='API 类型'):
        normalized_response(protocol,{'choices':[{'message':{'content':'wrong protocol'}}]})


@pytest.mark.parametrize('status,error,expected',[
    (429,{'code':'insufficient_quota','message':'private provider detail'},'quota_exceeded'),
    (400,{'type':'invalid_request_error','message':'Your credit balance is too low. private provider detail'},'insufficient_balance')])
def test_quota_and_credit_failures_are_distinct_from_transient_rate_limits(status,error,expected):
    calls=[]
    def handler(request):calls.append(1);return httpx.Response(status,json={'error':error})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError) as caught:
            ReplyEngine(client).generate(CONTEXT,{'base_url':'https://fixture.test','model':'fixture'})
    assert caught.value.code==expected and len(calls)==1
    assert 'private provider detail' not in str(caught.value)


def test_legacy_settings_remain_chat_and_protocol_saves_are_atomic(tmp_path):
    (tmp_path/'settings.json').write_text('{"base_url":"https://fixture.test/v1","model":"fixture"}',encoding='utf-8')
    settings=Settings(tmp_path)
    assert settings.public()['api_type']=='openai_chat'
    with pytest.raises(ValueError):settings.save({'api_type':'made-up','model':'wrong'})
    assert settings.values['model']=='fixture'
    settings.save({'api_type':'anthropic_messages'})
    assert Settings(tmp_path).public()['api_type']=='anthropic_messages'


def test_anthropic_cache_tokens_and_thinking_are_not_lost_or_double_counted():
    usage=usage_from_response({'usage':{'input_tokens':50,'cache_creation_input_tokens':20,
        'cache_read_input_tokens':100,'output_tokens':30,'output_tokens_details':{'thinking_tokens':10}}},'anthropic_messages')
    assert usage['prompt_tokens']==170 and usage['total_tokens']==200
    assert usage['reasoning_tokens']==10 and usage['prompt_cache_creation_tokens']==20


@pytest.mark.parametrize('protocol',PROTOCOLS)
def test_truncated_response_never_triggers_hidden_repair(protocol):
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        data=response(protocol,'{"choices":[')
        if protocol=='openai_chat':data['choices'][0]['finish_reason']='length'
        elif protocol=='openai_responses':data['status']='incomplete';data['incomplete_details']={'reason':'max_output_tokens'}
        else:data['stop_reason']='max_tokens'
        return httpx.Response(200,json=data)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError) as caught:
            ReplyEngine(client).generate(CONTEXT,{'base_url':'https://fixture.test','model':'fixture','api_type':protocol})
    assert caught.value.code=='output_limit' and len(calls)==1


def test_official_chat_uses_current_token_parameter_without_sampling_override():
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=response('openai_chat',json.dumps({'choices':CHOICES})))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(CONTEXT,{'base_url':'https://api.openai.com','model':'fixture','max_tokens':2048})
    assert calls[0]['max_completion_tokens']==2048 and 'max_tokens' not in calls[0]
    assert calls[0]['store'] is False and 'temperature' not in calls[0]


@pytest.mark.parametrize('protocol',PROTOCOLS)
def test_deepseek_type_change_preserves_non_thinking_mode_and_saved_output_budget(protocol,tmp_path):
    settings=Settings(tmp_path);settings.save({'api_type':protocol,'max_tokens':4096})
    expected={'reasoning':{'effort':'none'}} if protocol=='openai_responses' else {'thinking':{'type':'disabled'}}
    assert settings.model_config()['provider_extra']==expected
    assert Settings(tmp_path).values['max_tokens']==4096
    with pytest.raises(ValueError):settings.save({'max_tokens':100000,'model':'must-not-save'})
    assert settings.values['model']=='deepseek-flash'
