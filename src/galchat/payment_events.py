"""Payment-message metadata, never wallet operations or an account balance.

Mappings 1/3/4 match the researched message-card schema. Other subtypes stay
unknown; split-bill cards must not be described as a received red envelope.
"""
import re
import xml.etree.ElementTree as ET


TRANSFER_EVENTS = {'1': ('transfer_initiated', '转账发起记录'),
                   '3': ('transfer_receipt_recorded', '转账收款记录'),
                   '4': ('transfer_refund_recorded', '转账退还记录')}


def payment_root(content):
    if not isinstance(content, str) or len(content) > 256_000:
        return None
    if '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
        return None
    # Locally observed group wrapper. Strip only the complete sender line;
    # this does not grant that sender a current-chat identity.
    content = re.sub(r'^[A-Za-z0-9_.@-]{1,128}:\s*\n', '', content.strip(), count=1)
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    return root if root.tag == 'msg' else None


def unique_text(node, name, maximum=120):
    if node is None:
        return ''
    found = node.findall(name)
    if len(found) != 1 or list(found[0]):
        return ''
    text = (found[0].text or '').strip()
    if len(text) > maximum or any(ord(c) < 32 for c in text):
        return ''
    return text


def parse_payment(code, content):
    base, high = int(code) & 0xffffffff, int(code) >> 32
    if base != 49:
        return None
    root = payment_root(content)
    apps = root.findall('appmsg') if root is not None else []
    app = apps[0] if len(apps) == 1 else None
    subtype = unique_text(app, 'type', 20)
    candidate = subtype if subtype in {'2000', '2001'} else str(high)
    if candidate not in {'2000', '2001'}:
        return None
    kind = 'transfer' if candidate == '2000' else 'red_envelope'
    metadata = {'status': 'payment_event_only', 'event': 'payment_status_unknown',
                'status_basis': 'message_record_not_live_wallet', 'pay_subtype': ''}
    result = {'kind': kind, 'text': '[转账记录：状态未知]' if kind == 'transfer' else '[红包记录：领取状态未知]',
              'content_known': False, 'content_scope': 'event_only', 'local_type': int(code),
              'attachment': metadata}
    if (app is None or len(app.findall('type')) > 1 or
            (high and high != int(candidate)) or (subtype and subtype != candidate)):
        if app is not None:
            result.update(kind='payment', text='[支付记录：类型未确认]')
        return result
    payments = app.findall('wcpayinfo')
    if len(payments) != 1:
        return result
    pay = payments[0]
    result['content_known'] = True  # Event metadata only, never a complete wallet state.
    split = pay.find('newaa')
    if split is not None:
        result.update(kind='payment', text='[群收款／AA支付记录：状态未解析]')
        metadata['event'] = 'split_bill_record'
        return result
    if kind == 'transfer':
        state = unique_text(pay, 'paysubtype', 20)
        metadata['pay_subtype'] = state
        event, label = TRANSFER_EVENTS.get(state, ('payment_status_unknown', '转账记录：状态未知'))
        metadata['event'] = event
        amount = unique_text(pay, 'feedesc', 40)
        # Keep original display units; never guess that an integer is cents.
        if re.fullmatch(r'(?:[¥￥]|人民币\s*)?\s*\d{1,12}(?:\.\d{1,2})?\s*元?', amount):
            metadata['amount_display'] = amount
        memo = unique_text(pay, 'pay_memo', 120)
        if memo:
            metadata['memo'] = memo
        result['text'] = '[' + label + ']' + (' ' + metadata['amount_display'] if 'amount_display' in metadata else '')
    else:
        metadata['event'] = 'red_envelope_card'
        greeting = unique_text(pay, 'receivertitle', 120) or unique_text(pay, 'sendertitle', 120)
        if greeting:
            metadata['greeting'] = greeting
        result['text'] = '[红包卡片：领取状态未知]' + (' ' + greeting if greeting else '')
    return result
