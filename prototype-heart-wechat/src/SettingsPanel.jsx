import {useEffect,useRef,useState} from 'react';
import {ArrowCounterClockwise,ArrowLeft,CaretRight,Chats,GearSix,Heart,ImageSquare,Smiley,X} from '@phosphor-icons/react';
import {SETTINGS_PAGES,HISTORY_MARKS,historyPosition,historyValue,parseHistoryCount} from './settingsPages';
import {SceneSettings} from './SceneSettings';
import {emojiHelp} from './replyState';
import {API_TYPES,apiDescription} from './apiTypes';
import {SupportAuthor} from './SupportAuthor';

function Toggle({label,checked,onChange,disabled=false}) {
  return <input className="settings-toggle" type="checkbox" role="switch" aria-label={label}
    checked={Boolean(checked)} disabled={disabled} onChange={e=>onChange(e.target.checked)}/>;
}
function Radios({label,value,options,onChange,disabled}) {
  return <fieldset className="settings-radio-group"><legend>{label}</legend>
    {options.map(([key,text])=><label key={key}><input type="radio" name={label} value={key}
      checked={value===key} disabled={disabled} onChange={()=>onChange(key)}/>{text}</label>)}
  </fieldset>;
}
function RangeControl({label,value,min,max,onChange,onCommit,disabled,className='',valueText}) {
  const dirty=useRef(false);
  function commit(event) {
    const next=Number(event.currentTarget.value);
    if(!dirty.current)return;
    dirty.current=false;onCommit(next);
  }
  return <input type="range" aria-label={label} aria-valuetext={valueText}
    className={className} min={min} max={max} step="1" value={value} disabled={disabled}
    onChange={e=>{dirty.current=true;onChange(Number(e.target.value))}}
    onPointerUp={commit} onKeyUp={commit} onBlur={commit}/>;
}

export function SettingsPanel({prefs,state,contact,person,role,rolePreviews,roleNote,roleCount,scene,
  busy,saving,saveError,live,desktop,adult,groupChat,onUpdate,onRange,onPerson,onRole,onModal,
  onClose,onReset,onNative,onSetup,topicIntent,setTopicIntent,api,setApi,speech,setSpeech,initialPage='context'}) {
  const [page,setPage]=useState(initialPage);
  const previousPage=useRef(initialPage);
  const [resetting,setResetting]=useState(false);
  const [detailsOpen,setDetailsOpen]=useState(false);
  const [historyText,setHistoryText]=useState(String(prefs.history_limit));
  const [historyError,setHistoryError]=useState(false);
  const pane=useRef(null);
  const disabled=busy||saving||resetting;
  useEffect(()=>{if(pane.current)pane.current.scrollTop=0;setDetailsOpen(false)},[page]);
  useEffect(()=>{setHistoryText(String(prefs.history_limit));setHistoryError(false)},[prefs.history_limit]);
  function commitHistory() {
    if(disabled)return;
    const count=parseHistoryCount(historyText);
    if(count===null){setHistoryError(true);return;}
    setHistoryError(false);setHistoryText(String(count));
    if(count!==prefs.history_limit)onUpdate('history_limit',count);
  }
  async function reset() {
    if(disabled)return;
    setResetting(true);
    try { await onReset(page); }
    finally {setResetting(false)}
  }
  const history=historyPosition(prefs.history_limit);
  const uploading=prefs.media_policy!=='off';
  const titles={appearance:'对方角色',replies:'台词风格',context:'聊天上下文',display:'显示与窗口',connection:'连接设置'};
  const subtitle=groupChat?'群成员分别标注发言身份，双方消息都参与。':'包含双方消息，保留原来的时间和发言身份。';
  return <section className="config-area tabbed-settings" aria-label="设置">
    <article className="config-window settings-window">
      <header className="settings-header pywebview-drag-region"><h2>{page==='support'?<><Heart weight="fill" size={25}/>支持作者</>:<><GearSix weight="fill" size={27}/>CONFIG · 设置</>}</h2>
        <div className="settings-header-actions">{page!=='support'&&<button className="settings-support" onClick={()=>{previousPage.current=page;setPage('support')}}><Heart size={16}/>赞助</button>}
          <button className="settings-close" aria-label="关闭设置" onClick={onClose}><X size={24}/></button></div>
      </header>
      {page!=='support'&&<div className="settings-tabs" role="tablist" aria-label="设置分页">
        {SETTINGS_PAGES.map(([id,label])=><button key={id} id={`tab-${id}`} role="tab"
          aria-selected={page===id} aria-controls="settings-pane" tabIndex={page===id?0:-1}
          onClick={()=>setPage(id)} onKeyDown={e=>{
            if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
            e.preventDefault();const index=SETTINGS_PAGES.findIndex(([key])=>key===page);
            const next=e.key==='Home'?0:e.key==='End'?4:(index+(e.key==='ArrowRight'?1:4))%5;
            setPage(SETTINGS_PAGES[next][0]);e.currentTarget.parentElement.children[next].focus();
          }}>{label}</button>)}
      </div>}
      <div ref={pane} id="settings-pane" className={`settings-pane page-${page}`} role={page==='support'?'region':'tabpanel'} aria-label={page==='support'?'赞助作者':undefined} aria-labelledby={page==='support'?undefined:`tab-${page}`}>
        {page==='support'?<SupportAuthor/>:<h3 className="settings-page-title">{page==='context'&&<Chats size={20}/>}{titles[page]}</h3>}

        {page==='context'&&<>
          <p className="settings-intro">{subtitle}</p>
          <div className="history-control">
            <span>保留最近</span><div className="history-value-row">
              <input className="history-count-input" type="number" inputMode="numeric" min="10" max="5000" step="1"
                aria-label="精确读取条数" aria-invalid={historyError} aria-describedby="history-input-help"
                value={historyText} disabled={disabled} onChange={e=>{setHistoryText(e.target.value);setHistoryError(false)}}
                onBlur={commitHistory} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();commitHistory()}}}/><span>条</span>
            </div>
            <div className="history-slider">
              <RangeControl label="读取消息数量" value={history} min={0} max={1000} disabled={disabled}
                valueText={`${prefs.history_limit}条消息`} className="history-range"
                onChange={i=>onRange('history_limit',historyValue(i))}
                onCommit={i=>onUpdate('history_limit',historyValue(i))}/>
              <span className="history-progress" style={{width:`${history/10}%`}} aria-hidden="true"/>
              <img className="slider-gem" src="/assets/settings/slider-gem-v1.png" alt="" style={{left:`${history/10}%`}}/>
              <div className="history-ticks" aria-hidden="true">{HISTORY_MARKS.map(n=><span key={n} className={prefs.history_limit===n?'active':''} style={{left:`${historyPosition(n)/10}%`}}>
                <i/><small>{n}</small>
              </span>)}</div>
            </div>
            <p id="history-input-help" className={'settings-caption'+(historyError?' settings-input-error':'')} role={historyError?'alert':undefined}>
              {historyError?'请输入 10–5000 之间的整数。':'10–5000 条 · 默认 100 · 输入后按 Enter 保存'}</p>
          </div>
          <section className="settings-option-section generation-options">
            <div className="settings-option-main"><h4><GearSix weight="fill" size={20}/>按联系人询问辅助</h4>
              <Toggle label="按联系人询问辅助" checked={prefs.ask_before_assist===true} disabled={disabled} onChange={v=>onUpdate('ask_before_assist',v)}/></div>
            <p>{prefs.ask_before_assist?'本地识别自动进行；开启聊天后才向模型提交内容，选择会记住。':'所有聊天均可使用模型；开启此选项后逐人询问。'}</p>
          </section>
          <section className="settings-option-section generation-options">
            <div className="settings-option-main"><h4><Chats size={20}/>严格等待 3 秒</h4>
              <Toggle label="严格等待3秒" checked={prefs.strict_quiet_wait!==false} disabled={disabled} onChange={v=>onUpdate('strict_quiet_wait',v)}/></div>
            <p>{prefs.strict_quiet_wait!==false?'新消息重置计时，停发满 3 秒后生成。':'检测到新消息即可生成，在途新消息合并处理。'}</p>
          </section>
          <section className="settings-option-section media-options"><h4><ImageSquare size={20}/>图片与语音</h4>
            <label className="settings-select-row"><span>图片</span><select aria-label="自动读取图片数量" value={prefs.auto_images===false?0:(prefs.image_limit??3)}
              disabled={disabled||!uploading} onChange={e=>{const n=Number(e.target.value);onUpdate({image_limit:n,auto_images:n>0})}}>
              <option value={0}>不自动读</option>{[1,3,5].map(n=><option key={n} value={n}>最近 {n} 张</option>)}</select></label>
            <label className="settings-select-row"><span>表情包</span><select aria-label="自动读取表情包数量" value={prefs.sticker_limit??0}
              disabled={disabled||!uploading} onChange={e=>onUpdate('sticker_limit',Number(e.target.value))}>
              <option value={0}>忽略表情包</option><option value={1}>最近 1 张</option></select></label>
            <div className="settings-option-row"><div><span>微信语音转文字</span><small>将微信语音转为文字，参与上下文。</small></div>
              <Toggle label="微信语音转文字" checked={uploading&&prefs.auto_voice!==false} disabled={disabled||!uploading} onChange={v=>onUpdate('auto_voice',v)}/></div>
          </section>
          <details className="settings-details" open={detailsOpen} onToggle={e=>setDetailsOpen(e.currentTarget.open)}>
            <summary>读取范围与附件选项{!uploading?' · 上传已关闭':''}</summary>
            <div className="settings-option-row"><span>{groupChat?'此群的已记选择':'此人的已记选择'}</span><Toggle label="为当前聊天开启辅助" checked={person?.assistance_enabled===true} disabled={disabled||!contact||!prefs.ask_before_assist} onChange={v=>onPerson({assistance_enabled:v})}/></div>
            <p className="contact-assistance-note" title={contact?.name}>{contact?.name||'先打开一个聊天'} · {prefs.ask_before_assist?(person?.assistance_decided?(person.assistance_enabled?'已选择辅助':'已选择不辅助'):'还没选择，返回聊天即可选择'):'当前为所有聊天辅助，个人选择保留'}</p>
            <div className="settings-option-row"><span>自动生成总开关</span><Toggle label="自动生成总开关" checked={prefs.auto_generate} disabled={disabled} onChange={v=>onUpdate('auto_generate',v)}/></div>
            <p>{live?state?.history?.coverage:'体验内容为虚构示例。'}</p>
            {state?.history?.model_context&&<p>本轮纳入 {state.history.model_context.included_messages} 条，{state.history.model_context.omitted_messages} 条未纳入；长历史按模型预算优先最新内容与相关记录。</p>}
            <p>图片与表情包分别计数，包含双方发送的内容；读不到会跳过，继续参考已知文字，不猜图片内容。历史附件可手动加入。</p>
            <p>{prefs.strict_quiet_wait!==false?'每条新消息重置计时，停发满 3 秒后生成。':'关闭严格等待时，检测到更新即可生成；仍只运行一个生成任务。'}旧选项在新回复准备好前继续显示，可手动更新。额度或密钥错误会暂停自动生成，处理后点击恢复。</p>
            <p>语音使用微信转文字；微信在前台且输入框为空时自动读取，正在写消息时会等待。</p>
            <Radios label="附件处理" value={prefs.media_policy||'ask'} disabled={disabled}
              options={[["ask","备用语音逐次处理"],["auto","备用语音自动处理"],["off","关闭附件上传"]]} onChange={v=>onUpdate('media_policy',v)}/>
          </details>
        </>}

        {page==='appearance'&&<>
          <div className="appearance-heading"><span title={contact?.name}>{contact?.name||'请选择聊天'}</span>
            <button className="text-button" onClick={()=>onModal('roles')}>角色库 · {roleCount}位<CaretRight size={14}/></button></div>
          <div className="role-previews">{rolePreviews.map(r=><label className={'role-preview '+(role.id===r.id?'active':'')} key={r.id}>
            <img src={r.image} alt={r.name}/><span><input type="radio" name="counterpart" disabled={disabled||!contact}
              checked={role.id===r.id} onChange={()=>onRole(r.id)}/>{r.name}</span></label>)}</div>
          <p className="settings-note">{roleNote}。形象只影响外观。</p>
          <SceneSettings scene={scene} contact={contact} disabled={disabled} onChange={onPerson}/>
        </>}

        {page==='replies'&&<>
          <Radios label="说话方式" value={prefs.reply_style} disabled={disabled} options={[['playful','搞笑'],['serious','严肃'],['absurd','无厘头']]} onChange={v=>onUpdate('reply_style',v)}/>
          <div className="settings-scale"><span>无厘头</span><RangeControl label="无厘头到严肃" min={0} max={100} value={prefs.tone_balance} disabled={disabled}
            onChange={v=>onRange('tone_balance',v)} onCommit={v=>onUpdate('tone_balance',v)}/><span>严肃</span></div>
          <h4 className="settings-subtitle"><Smiley size={18}/>表情与颜文字</h4>
          <Radios label="表情方式" value={prefs.emoji_mode} disabled={disabled} options={[['off','关闭'],['natural','自然'],['lively','活泼']]} onChange={v=>onUpdate('emoji_mode',v)}/>
          <p className="settings-note">{emojiHelp[prefs.emoji_mode]}</p>
          <button className="text-button" onClick={()=>onModal('examples')}>看看回复示例<CaretRight size={14}/></button>
          <div className="settings-option-row inner-mode"><div><span>18+ 里模式</span><small>恋爱指导与台词批注</small></div>
            <Toggle label="18+里模式" checked={prefs.inner_mode} disabled={disabled||groupChat} onChange={v=>onUpdate('inner_mode',v)}/></div>
          {groupChat?<p className="settings-note">群聊不使用一对一恋爱指导。</p>:<button className="text-button" onClick={()=>onModal('pursuit')}>聊天目标与阶段<CaretRight size={14}/></button>}
          {!groupChat&&prefs.inner_mode&&!adult&&<p className="settings-note">在聊天目标中确认成年关系场景后，可显示批注。</p>}
          {live&&<details className="settings-details"><summary>想开个新话题</summary>
            <label className="settings-field">这次想说什么<input aria-label="这次想说什么" value={topicIntent} maxLength={300} onChange={e=>setTopicIntent(e.target.value)} placeholder="比如问问周末的打算"/></label>
            <p>仅用于这一次，不延续到后续轮次。</p><button className="secondary" disabled={disabled||!contact||!topicIntent.trim()} onClick={async()=>{
              const r=await onNative('generate',{intent:topicIntent});if(r){setTopicIntent('');onClose()}
            }}>按这个方向写台词</button></details>}
        </>}

        {page==='display'&&<>
          {[['font_size','文字大小',14,20],['dialogue_opacity','对白框不透明度',35,100],['text_speed','文字速度',10,100]].map(([key,label,min,max])=><label className="settings-display-range" key={key}>
            <span>{label}<small>{prefs[key]}{key==='dialogue_opacity'?'%':''}</small></span>
            <RangeControl label={label} value={prefs[key]} min={min} max={max} disabled={disabled}
              onChange={v=>onRange(key,v)} onCommit={v=>onUpdate(key,v)}/></label>)}
          <div className="settings-option-row"><span>逐字显示</span><Toggle label="逐字显示" checked={prefs.typewriter} disabled={disabled} onChange={v=>onUpdate('typewriter',v)}/></div>
          <div className="settings-option-row"><span>跟随微信窗口</span><Toggle label="跟随微信窗口" checked={prefs.follow_window} disabled={disabled} onChange={v=>onUpdate('follow_window',v)}/></div>
          <Radios label="微信最大化时" value={desktop?'hide':prefs.maximized_mode} disabled={disabled}
            options={desktop?[['hide','隐藏面板']]:[['hide','隐藏面板'],['overlay','透明覆盖预览']]} onChange={v=>onUpdate('maximized_mode',v)}/>
          {!desktop&&prefs.maximized_mode==='overlay'&&<label className="settings-display-range"><span>覆盖时不透明度</span><RangeControl label="覆盖时不透明度" min={10} max={60} value={prefs.overlay_opacity} disabled={disabled}
            onChange={v=>onRange('overlay_opacity',v)} onCommit={v=>onUpdate('overlay_opacity',v)}/></label>}
          <p className="settings-note">{desktop?'最大化时隐藏，恢复普通窗口后贴右显示。':'透明覆盖仅供原型预览。'}</p>
        </>}

        {page==='connection'&&<>
          {!live&&<p className="settings-note">这是虚构体验页，接口设置不会用于真实微信。</p>}
          {live&&state?.connection?.message&&<p className="settings-note">{state.connection.message}</p>}
          <label className="settings-field">聊天 API 类型<select aria-label="聊天 API 类型" value={api.type||'openai_chat'} disabled={disabled}
            onChange={e=>setApi('type',e.target.value)}>{API_TYPES.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
          <p className="settings-note protocol-description">{apiDescription(api.type||'openai_chat')}<br/>三种格式支持文字与图片，所选模型也需支持图片。</p>
          <label className="settings-field">API 地址<input aria-label="API 地址" value={api.endpoint} onChange={e=>setApi('endpoint',e.target.value)}/></label>
          <label className="settings-field">模型<input aria-label="模型" value={api.model} onChange={e=>setApi('model',e.target.value)}/></label>
          <label className="settings-field">API Key<input aria-label="API Key" type="password" autoComplete="off" value={api.key} onChange={e=>setApi('key',e.target.value)} placeholder={state?.settings?.api_key_set?'已保存，留空保留':'填写所选服务的密钥'}/></label>
          <button className="secondary" disabled={disabled||!live} onClick={async()=>{const r=await onNative('save_settings',{base_url:api.endpoint,model:api.model,api_type:api.type||'openai_chat',api_key:api.key});if(r)setApi('key','')}}>保存 API 设置</button>
          {live&&<div className="settings-connection-actions"><button className="text-button" disabled={disabled} onClick={()=>onNative('connect')}>检查当前聊天</button>
            <button className="text-button" disabled={disabled} onClick={()=>onNative('open_wechat')}>打开微信</button></div>}
          <details className="settings-details"><summary>模型输出上限</summary>
            <label className="settings-field">每次生成最多 Token<input aria-label="模型输出上限" type="number" min={256} max={4096}
              defaultValue={prefs.max_tokens??900} key={prefs.max_tokens??900} disabled={disabled}
              onBlur={e=>{const n=Number(e.target.value);if(Number.isInteger(n)&&n>=256&&n<=4096)onUpdate('max_tokens',n);
                else e.target.value=String(prefs.max_tokens??900)}}/></label>
            <p>默认 900，可调 256–4096。思考也可能占用输出额度；增大上限可能增加费用。事实核对至少使用 2200。</p>
          </details>
          <details className="settings-details"><summary>备用语音接口 · OpenAI Audio 兼容</summary><p>使用 Audio Transcriptions 格式，独立于聊天 API 类型。未配置备用密钥，仍可使用微信转文字。</p>
            <label className="settings-field">备用语音 API 地址<input aria-label="备用语音 API 地址" value={speech.endpoint} onChange={e=>setSpeech('endpoint',e.target.value)}/></label>
            <label className="settings-field">备用语音模型<input aria-label="备用语音模型" value={speech.model} onChange={e=>setSpeech('model',e.target.value)}/></label>
            <label className="settings-field">备用语音 API Key<input aria-label="备用语音 API Key" type="password" autoComplete="off" value={speech.key} onChange={e=>setSpeech('key',e.target.value)} placeholder={state?.settings?.asr_ready?'已保存，留空保留':'填写语音服务密钥'}/></label>
            <button className="secondary" disabled={disabled||!live} onClick={async()=>{const r=await onNative('save_settings',{asr_base_url:speech.endpoint,asr_model:speech.model,asr_api_key:speech.key});if(r)setSpeech('key','')}}>保存语音设置</button>
          </details>
          {live&&<details className="settings-details"><summary>兼容与应用选项</summary>
            <button className="text-button" disabled={disabled} onClick={onSetup}>框选聊天区域</button>
            <button className="text-button" disabled={disabled} onClick={()=>onNative('demo',{enabled:state?.mode!=='demo'})}>{state?.mode==='demo'?'返回真实微信':'虚构演示'}</button>
            {desktop&&<button className="text-button" disabled={disabled} onClick={()=>onNative('close')}>退出软件</button>}
          </details>}
        </>}
      </div>
      <footer className="settings-footer"><p role="status">{page==='support'?'感谢每一份喜欢与支持':saving||resetting?'正在保存…':saveError?'保存未完成，请重试':page==='connection'?'接口密钥需点击保存':'更改自动保存'}</p>
        <div>{page==='support'?<button className="settings-reset" onClick={()=>setPage(previousPage.current)}><ArrowLeft size={22}/>返回设置</button>:<button className="settings-reset" disabled={disabled||page==='appearance'&&!contact} onClick={reset}><ArrowCounterClockwise size={22}/>恢复本页</button>}
          <button className="settings-return" disabled={resetting} onClick={onClose}><ArrowLeft size={22}/>返回聊天</button></div>
      </footer>
    </article>
  </section>;
}
