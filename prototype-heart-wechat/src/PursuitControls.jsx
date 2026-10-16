import {pursuitGoals, pursuitStages, progressValue} from './pursuitOptions';
import {Diamond, Sparkle} from '@phosphor-icons/react';
import {RelationshipAssessment} from './RelationshipAssessment';

export function PursuitSettings({person, contact, disabled, onChange, assessment, generationStatus}) {
  const goal = pursuitGoals.find(([id]) => id === person?.pursuit_goal) || pursuitGoals[0];
  const stage = person?.pursuit_stage || 'unknown';
  return <section className="pursuit-settings" aria-label="聊天目标和阶段">
    <p className="help">{contact ? `只对${contact.name}生效。` : '先在微信打开一个聊天。'} 从认识到第一次约会，一次选好眼前这句话。</p>
    <RelationshipAssessment assessment={assessment} person={person} contact={contact}
      generationStatus={generationStatus} disabled={disabled} onChange={onChange}/>
    <fieldset disabled={disabled || !contact}><legend>这次想怎样聊</legend>
      <div className="pursuit-goal-options">{pursuitGoals.map(([id, label]) => <label key={id} className={goal[0] === id ? 'active' : ''}>
        <input type="radio" name="pursuit-goal" checked={goal[0] === id} onChange={() => onChange({pursuit_goal: id})}/>{label}
      </label>)}</div><p className="help goal-description">{goal[2]}</p>
      <label className="check-label pursuit-adult"><input type="checkbox" checked={Boolean(person?.adult_context)} onChange={e => onChange({adult_context: e.target.checked})}/>当前聊天是双方均成年的关系场景</label>
      {!person?.adult_context && <p className="help">确认后按目标提供关系接话；现在先用普通聊天。</p>}
    </fieldset>
    <fieldset disabled={disabled || !contact}><legend>记录已发生的进展</legend>
      <div className="pursuit-stage-options">{pursuitStages.map(([id, label]) => <button type="button" key={id} aria-pressed={stage === id} className={stage === id ? 'active' : ''} onClick={() => onChange({pursuit_stage: id})}>{label}</button>)}</div>
      <div className="stage-settings-note"><span>阶段由你确认，可以前进或退回。</span><button type="button" className="text-button" onClick={() => onChange({pursuit_stage: 'unknown'})}>还不确定</button></div>
    </fieldset>
  </section>;
}

export function PursuitProgress({person, disabled, onClick}) {
  const value = progressValue(person?.pursuit_stage, person?.adult_context);
  const label = value ? pursuitStages[value - 1][1] : '阶段待确认';
  return <button type="button" className="pursuit-progress" onClick={onClick} disabled={disabled}
    aria-label={`设置关系阶段，当前${label}`} data-stage={value}>
    <span className="pursuit-meter-title" aria-hidden="true">{value ? label : '待确认'}</span>
    <Sparkle className="pursuit-meter-ornament" size={10} weight="fill" aria-hidden="true"/>
    <span className="pursuit-track" role="progressbar" aria-label="已记录的互动进展" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={5} aria-valuenow={value} aria-valuetext={`${label}，由用户记录`}>
      <span className="pursuit-tube" aria-hidden="true">
        <span className="pursuit-core-window"><span className="pursuit-fill" style={{height:`${value / pursuitStages.length * 100}%`}}><img src="/assets/relationship-glass/liquid-core.png" alt="" draggable="false"/></span></span>
        <span className="pursuit-glass-frame"/>
      </span>
      <span className="pursuit-ticks" aria-hidden="true">{[...pursuitStages].reverse().map(([id, title], index) => <span key={id} className={'pursuit-tick ' + (pursuitStages.length - index <= value ? 'complete' : '') + (pursuitStages.length - index === value ? ' current' : '')}><Diamond size={7} weight="fill"/><span>{title}</span></span>)}</span>
    </span>
    <span className="pursuit-heart-medallion" aria-hidden="true"><img className="pursuit-heart" src="/assets/relationship-glass/heart-gem.png" alt="" draggable="false"/></span>
    <span className="pursuit-meter-label" aria-hidden="true">关系</span>
    <span className="pursuit-meter-tip" aria-hidden="true"><b>关系进展 · {label}</b><small>由你确认，点击调整</small></span>
  </button>;
}
