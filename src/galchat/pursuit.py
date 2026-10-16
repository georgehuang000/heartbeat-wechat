"""User-selected aims for the first-date scope; examples are fictional, never contact facts."""

DEFAULT_PURSUIT_GOAL = 'get_to_know'
PURSUIT_GOALS = {
    'get_to_know': ('先聊熟', '三条都围绕具体内容了解、接梗或分享当前反应；本次intent没有明确邀约要求时，不主动提出同行、见面或约会。'),
    'express_interest': ('表达兴趣', '帮助用户自然表达欣赏和想进一步了解的意愿；不假定对方也喜欢用户，不突然表白。'),
    'invite': ('试着邀约', '有合适话题时提出清楚、轻量且可拒绝的邀请；用户安排未知时不替用户定死时间地点。'),
}
PURSUIT_STAGES = {
    'unknown': '阶段待确认', 'new': '认识', 'talking': '聊熟',
    'invited': '已邀约', 'accepted': '已接受', 'scheduled': '已约好',
}

# Original examples, informed by the review in docs/skill-corpus-research.md.
# They demonstrate actions rather than reproducing a public figure's identity.
_EXAMPLES = {
    'get_to_know': [
        {'situation': '对方分享最近在找轻松的小说', 'reply': '想看轻松搞笑的，还是慢慢讲故事的？',
         'action': '从一个具体偏好接话，不编用户也读过某本书。'},
        {'situation': '对方只为课程事务加好友并道谢', 'reply': '不客气，名额还是以系统显示为准。',
         'action': '处理事务；礼貌、表情和求助不证明有约会意愿。'},
    ],
    'express_interest': [
        {'situation': '对方分享喜欢的小爱好，用户想表达兴趣', 'reply': '听你说这些挺有意思的，还想听你多讲一点。',
         'action': '表达用户当前想了解的意愿，不替对方定义关系。'},
        {'situation': '对方今天疲惫，暂时不想多聊', 'reply': '今天先好好休息，想聊的时候再聊。',
         'action': '当前需要先于长期目标，不为了升温延长对话。'},
    ],
    'invite': [
        {'situation': '对方主动聊起想去逛书店，用户想邀请同行', 'reply': '要不要一起逛逛？时间我们再找个都方便的。',
         'action': '清楚表达邀请，安排是待商量的提议。'},
        {'situation': '对方已经答应，问在哪里碰面', 'reply': '你从哪边过来方便？我们找个好碰面的地方。',
         'action': '处理已接受后的安排，不重新问愿不愿意去。'},
    ],
}

PURSUIT_INSTRUCTIONS = """context.pursuit.enabled=true表示用户已确认双方成年，并选择了当前联系人的聊天目标。
范围是从认识到第一次约会。目标是用户希望做什么，不是对方已经同意什么；不能据此编共同经历、关系阶段、好感分或成功率。
intent若表达了用户本次的具体决定，优先于长期目标。例如长期想邀约、本次不想去，三个选项都应表达本次拒绝。
优先回应末尾有效消息，再选择适合当前反馈的动作：接话、表达兴趣、轻量邀约、确认安排或留空间。
先聊熟时围绕具体话题，不强行安排邀约；表达兴趣时说用户自己的想法，不擅自替对方表态；试着邀约也不是每次都约。
对方只为办事求助，先处理事务，不把求助、感谢、表情和快回复当恋爱窗口。对方低落或想休息，先接住或收尾。
已经接受邀请时三个选项都处理最新的安排问题，不能仅调情或重复邀约；安排仍未知的部分问清或作为提议。
含糊地说以后再说时不追着要日期；明确说不去或别再约时三个方向都收住，不再追问拒绝原因，也不用换个活动继续邀请。
用户明确拒绝时不写“饭欠着/下次补/改天再约”，不把不愿意写成没空或没钱。用户已经发出完整邀约或收尾时可以等，不催回复。
用户本次不想赴约时，只用直接拒绝、谢意或歉意形成不同表达，不添加沙发、想独处、加班或生病等未提供的理由，也不把单独邀请编成一群人聚餐。
对方喜欢安静或想找小说，不等于用户也怕吵、缺书、想打发周末；邀请可表达当前想同行，但不能顺手添加这些个人背景。
已经发出完整邀约时第一条hint明确“可以先等，不必补发”；不追加“我都可以陪你/任意地方都行”等没有依据的时间或能力承诺。
context.pursuit.examples是虚构的方法示例，不是当前联系人的记录。不要逐字套用；其人名、活动、个人状态与安排不能移植成事实。
context.pursuit.confirmed_stage是用户手动记录的阶段，只表示用户确认过的进展，不是对方的好感或成功率。阶段不提供未记录的时间地点；最新消息若与阶段矛盾，优先回应最新反馈，不机械升级。
标签用短动作帮助选择，例如“顺着聊书”“表达想了解”“邀请同行”“确认碰面”“先留空间”。三个方向都要符合当前需要，不强凑升级档位。
没有确认成年关系语境时仅提供普通接话，不执行关系推进目标。"""


def pursuit_context(preferences: dict | None) -> dict:
    preferences = preferences if isinstance(preferences, dict) else {}
    goal = preferences.get('pursuit_goal', DEFAULT_PURSUIT_GOAL)
    if not isinstance(goal, str) or goal not in PURSUIT_GOALS:
        goal = DEFAULT_PURSUIT_GOAL
    enabled = preferences.get('adult_context') is True
    result = {'enabled': enabled, 'goal': goal, 'label': PURSUIT_GOALS[goal][0],
              'source': 'user_preference_not_counterpart_intent'}
    if enabled:
        result['focus'] = PURSUIT_GOALS[goal][1]
        result['examples'] = [dict(example) for example in _EXAMPLES[goal]]
        stage = preferences.get('pursuit_stage', 'unknown')
        if not isinstance(stage, str) or stage not in PURSUIT_STAGES:
            stage = 'unknown'
        result['confirmed_stage'] = stage
        result['stage_label'] = PURSUIT_STAGES[stage]
        result['stage_source'] = 'user_confirmed_not_model_scored'
    return result
