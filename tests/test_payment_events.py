"""No payment actions: validate parsed event evidence and outbound data scope."""
import json
from pathlib import Path

import httpx
import pytest

from galchat.attachments import parse_message, readiness
from galchat.engine import ReplyEngine
from galchat.engine import ReplyEngineError, _check_payment_claims
from galchat.memory import MemoryStore


def money(subtype=2000, state='1', extra='', amount='￥12.34'):
    return f'<msg><appmsg><type>{subtype}</type><wcpayinfo><paysubtype>{state}</paysubtype><feedesc>{amount}</feedesc>{extra}</wcpayinfo></appmsg></msg>'


@pytest.mark.parametrize('text',['收到了，刚到账','收到啦，钱先收下了','退款收到了，谢啦','退款收到，这事就清了','这笔就算结清啦','这钱我收得有点心虚','我已经领了红包','我还没点开红包','我还没细看'])
def test_unknown_payment_event_cannot_turn_into_user_receipt_or_unread_claim(text):
    context={'recent':[{'role':'other',**parse_message((2000<<32)|49,money())}]}
    with pytest.raises(ReplyEngineError):_check_payment_claims([{'text':text}],context)


def test_explicit_user_confirmation_supports_receipt_but_not_other_claims():
    context={'recent':[{'role':'other',**parse_message((2000<<32)|49,money())},
                       {'role':'self','kind':'text','content_known':True,'text':'我收到了'}]}
    _check_payment_claims([{'text':'收到了，谢谢'}],context)
    with pytest.raises(ReplyEngineError):_check_payment_claims([{'text':'刚到账了'}],context)
    context['recent'][-1]['text']='我还没收到，不知道是否到账'
    with pytest.raises(ReplyEngineError):_check_payment_claims([{'text':'收到了'}],context)


def test_payment_guard_allows_neutral_acknowledgment_and_unrelated_chat():
    context={'recent':[{'role':'other',**parse_message((2000<<32)|49,money())}]}
    _check_payment_claims([{'text':'收到提醒，我先核对一下状态'}],context)
    _check_payment_claims([{'text':'我先核对一下是否到账'}],context)
    _check_payment_claims([{'text':'收到了，谢谢'}],{'recent':[{'role':'other','text':'图片发你了'}]})


@pytest.mark.parametrize('state,event', [('1','transfer_initiated'), ('3','transfer_receipt_recorded'),
                                       ('4','transfer_refund_recorded'), ('0','payment_status_unknown'),
                                       ('8','payment_status_unknown'), ('9','payment_status_unknown')])
def test_transfer_status_only_uses_reviewed_card_codes(state,event):
    row=parse_message((2000<<32)|49,money(state=state))
    assert row['kind']=='transfer'
    assert row['attachment']['event']==event
    assert row['attachment']['amount_display']=='￥12.34'
    assert row['attachment']['status_basis']=='message_record_not_live_wallet'
    assert row['content_scope']=='event_only'
    assert readiness([{**row,'role':'other'}]) is None


def test_red_envelope_does_not_invent_a_received_amount_from_other_fields():
    row=parse_message((2001<<32)|49,money(2001,extra='<receivertitle>祝你开心</receivertitle><nativeurl>wxpay://secret</nativeurl><paymsgid>private-id</paymsgid>'))
    assert row['kind']=='red_envelope' and row['attachment']['event']=='red_envelope_card'
    assert '领取状态未知' in row['text']
    assert 'amount_display' not in row['attachment']
    assert 'wxpay' not in json.dumps(row) and 'private-id' not in json.dumps(row)


@pytest.mark.parametrize('subtype',[2000,2001])
def test_split_bill_is_not_described_as_an_ordinary_red_envelope(subtype):
    row=parse_message((subtype<<32)|49,money(subtype,extra='<newaa><newaatype>2</newaatype><receiverlist>private</receiverlist></newaa>'))
    assert row['kind']=='payment' and row['attachment']['event']=='split_bill_record'
    assert '红包' not in row['text'] and 'private' not in json.dumps(row)


def test_group_sender_wrapper_is_only_parsed_as_data_not_identity():
    xml=money(extra='<payer_username>private-payer</payer_username>')
    row=parse_message((2000<<32)|49,'wxid_synthetic:\n'+xml)
    assert row['content_known']
    assert 'wxid_synthetic' not in json.dumps(row)
    assert 'private-payer' not in json.dumps(row)


@pytest.mark.parametrize('extra',[
    '<paysubtype>3</paysubtype>',
    '<feedesc>￥999</feedesc>',
])
def test_ambiguous_duplicate_fields_are_not_first_wins(extra):
    row=parse_message((2000<<32)|49,money(extra=extra))
    if 'paysubtype' in extra:
        assert row['attachment']['event']=='payment_status_unknown'
    else:
        assert 'amount_display' not in row['attachment']


@pytest.mark.parametrize('amount',['','not money','99999999999999999','￥12.34<script>','￥12.345'])
def test_unverified_amounts_remain_absent(amount):
    row=parse_message((2000<<32)|49,money(amount=amount.replace('<','&lt;')))
    assert 'amount_display' not in row['attachment']


def test_conflicting_composite_type_never_becomes_known_payment_status():
    row=parse_message((2001<<32)|49,money(2000,state='3'))
    assert row['kind']=='payment' and not row['content_known']
    assert row['attachment']['event']=='payment_status_unknown'
    assert 'amount_display' not in row['attachment']


def test_missing_or_unsafe_xml_preserves_an_unknown_event():
    for body in ('', '<broken', '<!DOCTYPE msg><msg/>'):
        row=parse_message((2000<<32)|49,body)
        assert row['kind']=='transfer' and not row['content_known']
        assert row['attachment']['event']=='payment_status_unknown'


def test_event_metadata_survives_memory_and_real_request_shape(tmp_path):
    row=parse_message((2000<<32)|49,money(extra='<transferid>private-transaction</transferid><url>https://pay.invalid/private</url><pay_memo>测试备注</pay_memo>'))
    with MemoryStore(tmp_path/'memory.db') as store:
        store.upsert_messages('c','虚构联系人',[{**row,'role':'other','id':'m','timestamp':'2026-10-13T18:00:00'}])
        context=store.get_context('c')
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'choices':[
            {'label':f'方向{i}','text':f'第{i}种可选表达','hint':'仅事件','tone':'calm'} for i in range(3)]})}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(context,{'base_url':'https://example.test','model':'test'})
    body=json.loads(captured[0]['messages'][1]['content'])
    event=body['context']['recent'][0]['attachment']
    assert event['event']=='transfer_initiated' and event['amount_display']=='￥12.34'
    assert event['memo']=='测试备注' and event['scope']=='message_metadata'
    assert 'private-transaction' not in json.dumps(captured)
    assert 'pay.invalid' not in json.dumps(captured)

