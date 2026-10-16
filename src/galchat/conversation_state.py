"""Narrow explicit-closing fast path; elapsed time never terminates a chat."""
import re
from .timeline import parse_time

STATES = {'reply': 3, 'close': 1, 'wait': 0}


def only_own_messages(context, intent=''):
    """No peer in the available context cannot become a fictional peer reply.

    This does not infer that the full historical conversation had no peer.
    An explicit one-time direction can still request a new topic/follow-up.
    """
    if str(intent).strip():
        return False
    human = [m for m in context.get('recent', [])
             if m.get('role') in {'self', 'other'} and m.get('kind') != 'system']
    return bool(human) and all(m.get('role') == 'self' for m in human)


def own_closing(context, intent=''):
    if str(intent).strip():
        return None
    human = [m for m in context.get('recent', []) if m.get('role') in {'self', 'other'} and m.get('kind') != 'system']
    if not human:
        return None
    pattern = r'(?:好[的呀啊]?[,，、 ]*)?(?:晚安[啦呀啊哦～~]*|拜拜[啦呀啊哦～~]*|回头聊|下次聊|先这样[,， ]*(?:拜拜|晚安)|(?:那)?你(?:先)?(?:忙|休息|睡吧)[吧啦呀啊哦～~]*)(?:[。.!！～~ ]*)'
    last = human[-1]
    # Repeated reciprocal goodnight is acknowledgment, not a new obligation.
    if last.get('role') == 'other' and len(human) >= 2 and human[-2].get('role') == 'self':
        if context.get('conversation_kind') == 'group':
            return None
        if last.get('kind', 'text') != 'text' or not last.get('content_known', True) or not re.fullmatch(pattern, str(last.get('text', '')).strip()):
            return None
        before, after = parse_time(human[-2].get('timestamp')), parse_time(last.get('timestamp'))
        # A distant/undated new goodnight must go through semantic interpretation;
        # skipping the fast path does not declare that the old topic expired.
        if before is None or after is None or not 0 <= (after - before).total_seconds() < 3600:
            return None
        last = human[-2]
    if last.get('role') != 'self': return None
    if last.get('kind', 'text') != 'text' or not last.get('content_known', True) or last.get('truncated'):
        return None
    text = str(last.get('text', '')).strip()
    # Deliberately do not include acknowledgments, dates alone, pauses or silence.
    full = re.fullmatch(pattern, text)
    return {'text': text, 'message_id': last.get('id'), 'timestamp': last.get('timestamp'), 'reason': 'own_explicit_closing'} if full else None


def parse_state(data):
    state = data.get('conversation_state', 'reply')
    if state not in STATES:
        raise ValueError('conversation_state必须是reply、close或wait。')
    if not isinstance(data.get('choices'), list) or len(data['choices']) != STATES[state]:
        raise ValueError('继续聊需三个选项，轻松收尾需一个选项，等待对方需空选项。')
    return state


RHYTHM_INSTRUCTIONS = '''conversation_state表示现在需要什么：reply=3条；close=1条可选轻松收尾；wait=0条。
对方明确准备休息/结束且没有新的问题或事项时用close，给一句无需追问的轻松收尾。
用户已经完整收尾，或已经问完明确问题/发出完整邀约且尚等答复，不需要补充必要信息时用wait，不连续补发。
短促的“好/嗯”、表情、最后一条由自己发送本身不是结束依据；有新增问题、未完成事项、新话题或用户明确intent时仍可reply。
结合连续消息确认整件事是否已经处理完：用户已给出完整回应，对方仅确认接受，没有新问题、必要补充或intent时，可close给一句可选短收尾，或wait留空间。不要重复建议、索要进度、另加任务来凑reply三条。不根据某个词自动结束；同一句确认后有新问题仍为reply，普通分享后的短回应也不等于事情已完成。
不能只凭相邻间隔、消息过旧、附件未读就close/wait。一周或一个月后接回旧问题也要结合双方发言判断。
wait保留已有状态而非宣告永久结束；对方新增内容或用户明确想开口时重新判断。收尾不推动邀约/提问。'''
