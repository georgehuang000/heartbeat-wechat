export const pursuitGoals = [
  ['get_to_know', '先聊熟', '从具体话题聊起，先了解彼此。'],
  ['express_interest', '表达兴趣', '自然表达欣赏和想进一步了解的意愿。'],
  ['invite', '试着邀约', '找合适的话题，提出一个有余地的邀请。'],
];

export const pursuitStages = [
  ['new', '认识'], ['talking', '聊熟'], ['invited', '已邀约'],
  ['accepted', '已接受'], ['scheduled', '已约好'],
];

export function progressValue(stage, adult) {
  const value = pursuitStages.findIndex(([id]) => id === stage) + 1;
  return adult || value <= 2 ? value : 0;
}

export function demoPursuitChoices(person, who, prefs = {}) {
  let entries;
  if (who === 'demo-aning') {
    entries = [
      ['确认碰面', '书店门口碰面怎么样？你从哪边过来方便？', '回应碰面问题，地点作为提议。'],
      ['一起商量', '先选一家你过去方便的书店，再定碰面点？', '地点还未知，先商量交通方便的位置。'],
      ['确认时间', '周六下午你几点方便？时间定下来再找个好碰面的地方。', '已经答应了，处理安排而不是重复邀约。'],
    ];
  } else if (!person?.adult_context || person.pursuit_goal === 'get_to_know' || !person.pursuit_goal) {
    entries = [
      ['顺着聊书', '想看轻松搞笑的，还是慢慢讲故事的？', '从小说偏好接话，不急着转到邀约。'],
      ['接住分享', '逛书店很适合慢慢挑，不一定非要带着书单去。', '回应逛书店这个细节，不连续追问。'],
      ['了解兴趣', '你更喜欢随手翻到一本，还是先看推荐再去找？', '让对方聊自己的挑书习惯。'],
    ];
  } else if (person.pursuit_goal === 'express_interest') {
    entries = [
      ['表达想了解', '听你聊这些挺有意思的，还想听你多讲一点。', '表达想了解的意愿，不替对方判断关系。'],
      ['接住兴趣', '感觉你逛书店会有自己的挑书路线，想听听你怎么挑。', '关注对方的兴趣，留一个自然的话口。'],
      ['分享反应', '你这么一说，逛书店这件事听起来都更有意思了。', '说当下反应，不编自己读过的书。'],
    ];
  } else {
    entries = [
      ['邀请同行', '要不要一起逛逛？时间再找个都方便的。', '提出清楚的邀请，安排留待商量。'],
      ['先聊偏好', '有想去的书店吗？先挑个你喜欢的方向。', '不急着定安排，也不凭空编店名。'],
      ['轻松提议', '要不一起去挑书？逛累了就找地方坐一会儿。', '轻量提议，对方可以选择接受或拒绝。'],
    ];
  }
  if (who === 'demo-xiaolin' && (!person?.adult_context || !person.pursuit_goal || person.pursuit_goal === 'get_to_know')
      && (prefs.reply_style === 'absurd' || prefs.tone_balance <= 15)) {
    entries[1] = ['轻松接梗', '先挑一本能让脑袋放假的，书店也该有精神按摩区。', '用轻松比喻接书店话题，不编个人经历。'];
  }
  return entries.map(([label, text, hint], i) => ({id: `demo-${i}`, label,
    text: text + (prefs.emoji_mode === 'lively' && i === 0 ? ' 🙂' : ''), hint, tone: i === 0 ? 'warm' : 'curious'}));
}

export function demoPursuitMessages(who) {
  if (who === 'demo-aning') return [
    {id: 'demo-invite', role: 'self', kind: 'text', text: '周六一起去逛书店？', source: 'demo'},
    {id: 'demo-accepted', role: 'other', kind: 'text', text: '周六下午可以，在哪里碰面？', source: 'demo'},
  ];
  return [{id: 'demo-message', role: 'other', kind: 'text', text: '这周想去逛逛书店，找点轻松的小说。', source: 'demo'}];
}
