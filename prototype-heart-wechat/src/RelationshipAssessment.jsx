import {useState} from 'react';
import {pursuitStages} from './pursuitOptions';
import {canAdoptAssessment} from './assessmentData';

export function RelationshipAssessment({assessment, person, contact, generationStatus, disabled, onChange}) {
  const [saving, setSaving] = useState(false);
  const candidate = assessment?.status === 'suggested';
  const label = pursuitStages.find(([id]) => id === assessment?.stage)?.[1];
  const adoptable = canAdoptAssessment(assessment, person);
  async function adopt() {
    if (!adoptable || disabled || saving) return;
    setSaving(true);
    try { await onChange({pursuit_stage: assessment.stage}); }
    finally { setSaving(false); }
  }
  return <section className="relationship-assessment" aria-label="聊天记录预判">
    <h4>聊天记录预判{candidate && <strong>建议 · {label}</strong>}</h4>
    <p>{assessment?.reason || (!contact ? '打开聊天后，根据读到的记录预判。'
      : generationStatus === 'generating' ? '正在结合这次聊天判断…'
      : generationStatus === 'unconfigured' ? '连接模型后，生成台词时会一起预判阶段。'
      : '这次暂无可核对的阶段建议，可以先手动记录。')}</p>
    {assessment && <small className="assessment-scope">{assessment.source === 'demo' ? '虚构演示 · ' : ''}
      范围：本次读取{assessment.scope?.read_count || 0}条消息
      {assessment.scope?.truncated ? '，部分记录未纳入或文字截断' : ''}</small>}
    {assessment?.evidence?.length > 0 && <ul className="assessment-evidence">{assessment.evidence.map(item => <li key={item.message_id}>
      <span>{item.role === 'self' ? '你' : contact?.name || '对方'}{item.timestamp ? ` · ${item.timestamp.replace('T', ' ').slice(0, 16)}` : ''}</span>
      <q>{item.quote}</q>
    </li>)}</ul>}
    {candidate && <div className="assessment-action"><button type="button" className="secondary"
      disabled={disabled || saving || !adoptable} onClick={adopt}>
      {saving ? '正在保存…' : person?.pursuit_stage === assessment.stage ? `已记录为「${label}」` : `采用「${label}」`}
    </button><small>确认后记录为当前阶段。</small></div>}
    <small className="assessment-boundary">依据双方实际发言判断互动进展，记录不足时保留待确认。</small>
  </section>;
}
