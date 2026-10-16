import json
import httpx
import pytest
from galchat.api_usage import ApiUsageLog
from galchat.engine import ReplyEngine, ReplyEngineError


def test_success_and_unfunded_review_are_counted_without_logging_secrets(tmp_path):
    ledger = ApiUsageLog(tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={'id':'fictional-request',
                'choices':[{'message':{'content':'private generated answer'}}],
                'usage':{'prompt_tokens':300,'completion_tokens':20,'total_tokens':320,
                    'prompt_cache_hit_tokens':200,'prompt_cache_miss_tokens':100}})
        return httpx.Response(402, json={'error':'private provider explanation'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        engine = ReplyEngine(client, usage_observer=ledger.record)
        headers = {'Authorization':'Bearer private-api-key'}
        body = {'model':'fictional-model','messages':[{'role':'user','content':'private message text'}]}
        engine._request('POST','https://api.example.test/chat/completions',headers,10,body)
        with pytest.raises(ReplyEngineError):
            engine._request('POST','https://api.example.test/chat/completions',headers,10,body)
    report = ledger.summary('api.example.test')
    assert report['requests'] == 2 and report['model_requests'] == 2
    assert report['failed_requests'] == 1
    assert report['total_tokens'] == 320
    assert report['unknown_usage_requests'] == 1
    assert report['historical_usage_recovered'] is False
    text = ledger.path.read_text('utf-8')
    assert all(secret not in text for secret in ('private-api-key','private message text','private generated answer','private provider explanation'))
    assert ledger.summary('different.example.test')['requests'] == 0


def test_telemetry_callback_failure_never_changes_a_valid_model_response():
    def broken_observer(event):
        raise OSError('disk full')
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,json={'choices':[],
            'usage':{'completion_tokens_details':'malformed optional metrics'}}))) as client:
        result = ReplyEngine(client,usage_observer=broken_observer)._request('POST','https://api.example.test/test',{},10,{})
    assert result['choices'] == []
