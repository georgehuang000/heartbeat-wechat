"""Conversation and speaker identities; names never establish ownership."""
import hashlib
import re

MIN_HISTORY = 10
MAX_HISTORY = 5000
HISTORY_LIMITS = range(MIN_HISTORY, MAX_HISTORY + 1)
GROUP_INSTRUCTIONS = '''当conversation_kind=group时，这是群聊，接收者是group_chat，而不是chat_peer。
wechat_user仍是代写者；其他每个group_member_*分别属于不同群成员，显示名相同也不能合并。
用每条原消息的speaker_id理解我/我的；群里“你”未必指用户，不能机械套用一对一代词。
优先回应最新相关话题，结合明确提及/上下文判断是否需要用户接话；别人互聊或事情已完整解决可wait。
候选可回应明确的一位或面向全群，不能替群成员发言、承诺或把A的经历写给B。respond_to_speaker_id仅标最新发言人，不证明他在对用户说话。
转发/引用作者仍属于quoted_author，不能凭同名绑定成任何群成员。群聊不输出恋爱阶段、好感推测或一对一亲密推进；relationship_assessment=null。
审稿的owner_id和speaker_id可以是participants列出的具体group_member_*；禁止用不存在的chat_peer统称所有群成员。'''


def is_group(contact):
    return (contact or {}).get('conversation_kind') == 'group'


def group_participant(account, room, sender):
    return 'group_member_' + hashlib.sha256(
        (account + '\0' + room + '\0' + sender).encode()).hexdigest()[:24]


def model_speaker(message, group=False):
    if message.get('role') == 'self':
        return 'wechat_user'
    if message.get('role') == 'system':
        return 'system'
    if message.get('role') != 'other':
        return None
    if not group:
        return 'chat_peer'
    value = message.get('participant_id')
    return value if isinstance(value, str) and re.fullmatch(r'group_member_[0-9a-f]{24}', value) else None
