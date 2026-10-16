import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Clock,GearSix,Diamond,CaretRight,X,Minus,Plus,ToggleLeft,ToggleRight,CheckCircle,Plugs,Smiley,Chats} from '@phosphor-icons/react';
import {roles,ageBands} from './roles';
import {call,live,desktop} from './bridge';
import {RichMessage} from './RichMessage';
import {AttachmentPanel} from './AttachmentPanel';
import {PursuitProgress,PursuitSettings} from './PursuitControls';
import {pursuitGoals,demoPursuitChoices,demoPursuitMessages} from './pursuitOptions';
import {resolveScene} from './scenes';
import {SettingsPanel} from './SettingsPanel';
import {SETTINGS_DEFAULTS,pageDefaults} from './settingsPages';
import {currentAssessment, demoAssessment} from './assessmentData';
import {ActivityIndicator} from './ActivityIndicator';
import {ReplyStatus} from './ReplyStatus';
import {ContactAssistPrompt} from './ContactAssistPrompt';
import {acceptServerState,replyUpdateLabel,replyActivity,emojiHelp} from './replyState';
const defaults=SETTINGS_DEFAULTS;
const examples=[['接住情绪','辛苦了，今天被什么事折腾了？','回应具体经历，愿意说再继续。'],['轻松接梗','今日电量耗尽，先吃饭再做人 🫠','熟悉时用玩笑缓和疲惫，难过时先认真关心。'],['留点余地','今晚允许躺平，明天再努力 (￣▽￣)','允许休息，不要求对方马上解释。']];
const labels={off:'关闭',natural:'自然',lively:'活泼',playful:'搞笑',serious:'严肃',absurd:'无厘头'};
function stored(key,fallback){try{return JSON.parse(localStorage.getItem(key))||fallback}catch{return fallback}}
function IconButton({label,children,onClick}){return <button type="button" className="icon-button" aria-label={label} onClick={onClick}>{children}</button>}
function RadioOptions({name,value,options,onChange}){return <div className="inline-options">{options.map(([id,label])=><label className="radio-label" key={id}><input type="radio" name={name} value={id} checked={value===id} onChange={()=>onChange(id)}/>{label}</label>)}</div>}
export function App(){
 const [state,setState]=useState(null),[prefs,setPrefs]=useState(()=>({...defaults,...stored('heart-wechat:settings-v2',{})})),[config,setConfig]=useState(!desktop),[selected,setSelected]=useState(null),[modal,setModal]=useState(null),[input,setInput]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[demoContact,setDemoContact]=useState('demo-xiaolin'),[contactPrefs,setContactPrefs]=useState(()=>stored('heart-wechat:contacts-v2',{})),[minimized,setMinimized]=useState(false),[dialogue,setDialogue]=useState(''),[age,setAge]=useState('all'),[gender,setGender]=useState('all'),[custom,setCustom]=useState({}),[customName,setCustomName]=useState(''),[previewMax,setPreviewMax]=useState(false),[apiKey,setApiKey]=useState(''),[endpoint,setEndpoint]=useState('https://api.deepseek.com'),[model,setModel]=useState('deepseek-flash');
 const dialogRef=useRef(null),lastClicked=useRef(null),calibration=useRef(null),[setupImage,setSetupImage]=useState(null),[setupRects,setSetupRects]=useState([]),[accountName,setAccountName]=useState(''),[dragBox,setDragBox]=useState(null),dragStart=useRef(null);
 const gameRef=useRef(null),[sticker,setSticker]=useState(null),[stickerError,setStickerError]=useState('');
 const [attachment,setAttachment]=useState(null),[asrKey,setAsrKey]=useState(''),[asrEndpoint,setAsrEndpoint]=useState('https://api.siliconflow.cn/v1'),[asrModel,setAsrModel]=useState('FunAudioLLM/SenseVoiceSmall');
 const editingPrefs=useRef(new Set()),latestServerState=useRef(null),pressedReply=useRef(null);
 const contact=live?state?.contact:{id:demoContact,name:demoContact==='demo-xiaolin'?'小林':'阿宁'};
 const identity=contact?.id||'unconnected';
 const groupChat=contact?.conversation_kind==='group';
 const [historyVisible,setHistoryVisible]=useState(100);
 const person=live?state?.contact_preferences:(contactPrefs[identity]||{role:demoContact==='demo-xiaolin'?'middle-male':'young-female',adult_context:false});
 const role=custom[identity]||roles.find(r=>r.id===person?.role)||{id:'pending',name:'待选形象',image:'/assets/app-icon.png'};
 const scene=resolveScene(person,identity);
 const rolePreviews=[...(role.id==='pending'?[]:[role]),...roles.filter(r=>r.id!==role.id&&['young','urban'].includes(r.age))].slice(0,4);
 const roleNote=!contact?'正在识别当前联系人…':person?.role_source==='manual'?'已保留你为这个聊天选择的形象':groupChat?'群聊使用伙伴立绘，成员身份分别标注':person?.role_source==='default'?'性别未知，默认使用黑长直御姐；可以更换形象':'按资料性别默认青年形象，实际年龄未知';
 const choices=live?(state?.assistance?.enabled===false?[]:state?.choices||[]):demoPursuitChoices(person,demoContact,prefs);
 const activity=replyActivity(state);
 const activityPaused=live&&(state?.activity?.active===false||state?.activity?.needs_refresh===true);
 const messages=live?(state?.messages||[]):demoPursuitMessages(demoContact);
 const assessment=groupChat?null:live?currentAssessment(state?.relationship_assessment,identity,state?.revision):demoAssessment(identity,messages,person);
 const latest=[...messages].reverse().find(m=>m.role==='other');
 const dialogueSpeaker=contact?.name;
 const lastHuman=[...messages].reverse().find(m=>['self','other'].includes(m.role));
 const afterOwnReply=lastHuman?.role==='self';
 const latestIsEmoji=lastHuman?.kind==='emoji'&&!lastHuman?.content_known;
 const dateLabel=latest?.timestamp?.replace('T',' ').slice(0,16)||'';
 const lastAction=lastHuman&&lastHuman.id!==latest?.id?`你 · ${lastHuman.timestamp?.replace('T',' ').slice(0,16)||'时间未知'} · ${lastHuman.kind==='text'?'已回复':({card:'发送名片',call:'通话记录',image:'发送图片',voice:'发送语音',forward:'转发记录'})[lastHuman.kind]||'发送附件'}`:'';
 function openAttachment(m){setAttachment(m);setModal('attachment')}
 const message=live&&state?.assistance?.enabled===false?'为这个聊天开启辅助后，再一起想下一句。':latest?.text||(contact?'当前记录已读取，暂没有对方发言。':'打开微信中的一个聊天，故事就从这里开始。');
 const adult=!groupChat&&Boolean(person?.adult_context),showGuidance=prefs.inner_mode&&adult;
 const goalLabel=(pursuitGoals.find(([id])=>id===person?.pursuit_goal)||pursuitGoals[0])[1];
 const [topicIntent,setTopicIntent]=useState('');
 const [deferredContact,setDeferredContact]=useState(null);
 const [choosingAssist,setChoosingAssist]=useState(false);
 useEffect(()=>setDeferredContact(null),[identity,state?.settings?.ask_before_assist]);
 async function chooseAssistance(enabled){if(choosingAssist)return;setChoosingAssist(true);try{await setPerson({assistance_enabled:enabled})}finally{setChoosingAssist(false)}}
 useEffect(()=>setTopicIntent(''),[identity]);
 useEffect(()=>setHistoryVisible(100),[identity,modal]);
 const [explained,setExplained]=useState(null),pressTimer=useRef(null),suppressClick=useRef(null),pressPoint=useRef(null);
 function endPress(){clearTimeout(pressTimer.current);pressTimer.current=null;pressPoint.current=null;}
 function startPress(e,choice){pressedReply.current={choice,contact_id:identity,revision:state?.revision};if(e.pointerType!=='touch')return;endPress();pressPoint.current=[e.clientX,e.clientY];pressTimer.current=setTimeout(()=>{suppressClick.current=choice.id;setExplained(choice.id)},450)}
 function movePress(e){if(pressPoint.current&&Math.hypot(e.clientX-pressPoint.current[0],e.clientY-pressPoint.current[1])>10)endPress()}
 useEffect(()=>()=>endPress(),[]);
 useEffect(()=>{endPress();suppressClick.current=null;pressedReply.current=null;setExplained(null);setSelected(null);lastClicked.current=null;},[identity,person?.pursuit_goal,person?.pursuit_stage,person?.adult_context,state?.reply_epoch]);
 useEffect(()=>{endPress();setExplained(null);},[modal]);
 useEffect(()=>{setSelected(null);},[state?.reply_batch?.id]);
 const serverPrefs=(s)=>{if(!acceptServerState(latestServerState.current,s))return;latestServerState.current=s;setState(s);setPrefs(p=>({...p,...Object.fromEntries(Object.entries(s.settings).filter(([k])=>!editingPrefs.current.has(k)))}));};
 function changeRange(key,value){editingPrefs.current.add(key);setPrefs(p=>({...p,[key]:value}));}
 useEffect(()=>{if(state?.settings){setEndpoint(state.settings.base_url);setModel(state.settings.model)}},[state?.settings?.base_url,state?.settings?.model]);
 useEffect(()=>{if(state?.settings){setAsrEndpoint(state.settings.asr_base_url);setAsrModel(state.settings.asr_model)}},[state?.settings?.asr_base_url,state?.settings?.asr_model]);
 useEffect(()=>{if(!live)return;let stopped=false,t;const poll=async()=>{try{const s=await call('get_state');if(!stopped)serverPrefs(s)}catch(e){if(!stopped)setNotice(e.message)}finally{if(!stopped)t=setTimeout(poll,450)}};poll();return()=>{stopped=true;clearTimeout(t)}},[]);
 useEffect(()=>{if(!live){localStorage.setItem('heart-wechat:settings-v2',JSON.stringify(prefs));localStorage.setItem('heart-wechat:contacts-v2',JSON.stringify(contactPrefs))}},[prefs,contactPrefs]);
 useEffect(()=>{setSelected(null);setInput('');lastClicked.current=null;if(modal==='attachment'){setAttachment(null);setModal(null)}},[identity,state?.reply_epoch]);
 useEffect(()=>{if(!prefs.typewriter){setDialogue(message);return}let i=0;setDialogue('');const t=setInterval(()=>{setDialogue(message.slice(0,++i));if(i>=message.length)clearInterval(t)},190-prefs.text_speed);return()=>clearInterval(t)},[message,prefs.typewriter,prefs.text_speed]);
 useEffect(()=>{if(modal)dialogRef.current?.showModal();else dialogRef.current?.close()},[modal]);
 useEffect(()=>{if(!notice)return;const t=setTimeout(()=>setNotice(''),2000);return()=>clearTimeout(t)},[notice]);
 useEffect(()=>{if(activityPaused)return;let stale=false;setSticker(null);setStickerError('');if(!live||!latest?.has_sticker)return;call('get_sticker',{id:latest.id,contact_id:identity,revision:state.revision}).then(r=>{if(!stale){setSticker(r.data_url);setStickerError(r.message||'')}}).catch(e=>{if(!stale)setStickerError(e.message)});return()=>{stale=true}},[latest?.id,latest?.has_sticker,identity,state?.revision,activityPaused]);
 useLayoutEffect(()=>{
  if(!desktop||config||!gameRef.current)return;
  const root=gameRef.current;
  const fit=()=>{root.querySelectorAll('.choice').forEach(button=>{
   const copy=button.querySelector('.choice-copy');if(!copy)return;
   let size=prefs.font_size*(root.clientWidth>400?1.25:1.12);button.style.setProperty('--fitted-size',size+'px');
   const style=getComputedStyle(button),available=button.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
   while(size>12&&(copy.scrollHeight>available+1||copy.scrollWidth>copy.clientWidth+1)){size=Math.max(12,size-.5);button.style.setProperty('--fitted-size',size+'px')}
  })};
  fit();const observer=new ResizeObserver(fit);observer.observe(root);return()=>observer.disconnect();
 },[choices,prefs.font_size,config,afterOwnReply,latestIsEmoji]);
 const [apiType,setApiType]=useState(defaults.api_type);
 useEffect(()=>{if(state?.settings)setApiType(state.settings.api_type||'openai_chat')},[state?.settings?.api_type]);
 const [settingsSaving,setSettingsSaving]=useState(false),[settingsSaveError,setSettingsSaveError]=useState(false);
 async function savePrefs(values){
  const keys=Object.keys(values),before=prefs;
  keys.forEach(key=>editingPrefs.current.add(key));setPrefs(p=>({...p,...values}));
  setSettingsSaving(true);setSettingsSaveError(false);
  try {if(live){const r=await call('save_settings',values);serverPrefs(r.state)}return true}
  catch(e){if(e.state)serverPrefs(e.state);const source=e.state?.settings||latestServerState.current?.settings||before;
   setPrefs(p=>({...p,...Object.fromEntries(keys.map(key=>[key,source[key]]))}));setSettingsSaveError(true);setNotice(e.message);return false}
  finally{keys.forEach(key=>editingPrefs.current.delete(key));setSettingsSaving(false)}
 }
 async function update(key,value){return savePrefs(typeof key==='object'?key:{[key]:value})}
 async function resetPage(page){
  if(page==='appearance'){
   if(!contact)return false;
   let result;
   if(live)result=await setPerson({reset_appearance:true});
   else{setContactPrefs(p=>{const c={...p};const saved={...c[identity]};delete saved.role;delete saved.role_source;delete saved.scene_id;
    c[identity]={...saved,role:demoContact==='demo-xiaolin'?'middle-male':'young-female'};return c});result=true}
   if(result){setCustom(p=>{const c={...p};delete c[identity];return c});setNotice('已恢复本页形象与背景')}
   return result;
  }
  const values=pageDefaults(page);const result=await savePrefs(values);
  if(result){
   if(page==='replies')setTopicIntent('');
   if(page==='connection'){setApiType(values.api_type);setEndpoint(values.base_url);setModel(values.model);setApiKey('');setAsrEndpoint(values.asr_base_url);setAsrModel(values.asr_model);setAsrKey('')}
   setNotice(page==='connection'?'已恢复本页接口选项，已存密钥保留':'已恢复本页设置');
  }
  return result;
 }

 async function setPerson(value){if(!contact){setNotice('先打开一个聊天，再设置当前对象');return false}if(live){try{const r=await call('save_contact_preferences',{contact_id:identity,revision:state.revision,...value});serverPrefs(r.state);return true}catch(e){setNotice(e.message);return false}}else{setContactPrefs(p=>({...p,[identity]:{...person,...value}}));return true}}
 async function choose(choice){const captured=pressedReply.current;pressedReply.current=null;if(captured)choice=captured.choice;if(suppressClick.current===choice.id){suppressClick.current=null;return}if(busy)return;setBusy(true);try{if(live){const r=await call('choose_fill',{id:choice.id,batch_id:choice.batch_id,contact_id:captured?.contact_id||identity,revision:captured?.revision??state.revision});serverPrefs(r.state);setNotice(r.message||'已填入微信，未发送')}else{if(input&&input!==lastClicked.current?.text)throw new Error('演示草稿已由你编辑，已保留原文；清空后可再选。');setInput(choice.text);setNotice('已填入演示输入框，未发送')}setSelected(choice.id);lastClicked.current=choice;setExplained(null)}catch(e){if(e.state)serverPrefs(e.state);setNotice(e.message)}finally{setBusy(false)}}
 async function native(method,payload={}){setBusy(true);try{const r=await call(method,payload);if(r.state)serverPrefs(r.state);if(!['connect','retry_latest_image'].includes(method))setNotice(r.message||'已完成');return r}catch(e){if(e.state)serverPrefs(e.state);setNotice(e.message)}finally{setBusy(false)}}
 async function startSetup(){const r=await native('visual_setup_capture');if(r){setSetupImage(r);setSetupRects([]);setAccountName(r.account_name||'');setModal('setup')}}
 function point(e){const b=calibration.current.getBoundingClientRect();return [Math.round((e.clientX-b.left)*setupImage.width/b.width),Math.round((e.clientY-b.top)*setupImage.height/b.height)]}
 function finishBox(e){if(!dragStart.current)return;const a=dragStart.current,b=point(e);dragStart.current=null;setDragBox(null);if(Math.abs(b[0]-a[0])>8&&Math.abs(b[1]-a[1])>8)setSetupRects(p=>[...p.slice(0,2),[Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[0],b[0]),Math.max(a[1],b[1])]])}
 const visibleRoles=roles.filter(r=>(age==='all'||r.age===age)&&(gender==='all'||r.gender===gender));
 const hiddenPreview=!live&&previewMax&&prefs.maximized_mode==='hide';
 return <main className={'design-board '+(desktop?'desktop-runtime':'')} onPointerDown={e=>{if(!e.target.closest?.('.choice'))setExplained(null)}}>
 {!desktop&&<header className="identity-row"><img className="hero-wordmark" src="/assets/wordmark.png" alt="心动微信"/><div className="app-icon-area"><span>Logo / 图标</span><img src="/assets/app-icon.png" alt="心形对话气泡应用图标"/></div></header>}
 <div className={'workspace '+(config?'':'single')}><section className="dialogue-area" aria-label="对白浮窗"><h1 className="area-label">对白浮窗</h1>
 {hiddenPreview?<div className="hidden-preview"><Minus size={28}/><p>微信最大化时，面板已收起</p><button className="secondary" onClick={()=>setPreviewMax(false)}>模拟恢复窗口</button></div>:<article ref={gameRef} className={'game-window '+(!live&&previewMax?'max-overlay':'')} style={!live&&previewMax?{opacity:prefs.overlay_opacity/100}:undefined}><header className="window-bar pursuit-window-bar pywebview-drag-region"><img className="window-wordmark" src="/assets/wordmark.png" alt="心动微信"/><button className="pursuit-goal-button" aria-label={groupChat?"群聊设置":"设置聊天目标与阶段"} disabled={busy||!contact} onClick={()=>groupChat?setConfig(true):setModal('pursuit')}>{groupChat?'群聊设置':adult?`目标 · ${goalLabel}`:'设置目标'}</button><div className="window-controls"><IconButton label={desktop&&live?'最小化窗口':minimized?'展开对白':'收起对白'} onClick={()=>desktop&&live?native('minimize'):setMinimized(!minimized)}><Minus size={22}/></IconButton><IconButton label={desktop&&live?"关闭窗口":"收起对白窗口"} onClick={()=>desktop&&live?native("close"):setMinimized(true)}><X size={23}/></IconButton></div></header>
 {!minimized&&<><div className="pursuit-content"><div className="choice-list" role="group" aria-label="选择回复" style={{fontSize:(prefs.font_size+2)*1.34,'--copy-size':prefs.font_size+'px'}}>{choices.length?<>{live&&<ReplyStatus activity={activity} detail={replyUpdateLabel(state?.reply_batch,state?.generation)} busy={busy} onUpdate={()=>native("generate")} onSettings={()=>setConfig(true)}/>}{state?.generation?.conversation_state==='close'&&<p className="choice-context-note">轻松收个尾，也可以不发。</p>}{(afterOwnReply||latestIsEmoji||lastAction)&&<p className="choice-context-note">{lastAction||afterOwnReply&&"你已回复，可等待对方。"} {latestIsEmoji&&!lastHuman?.visual_input&&"表情含义未识别，按已知内容接话。"}</p>}{choices.map(choice=><button className={'choice '+(selected===choice.id?'selected':'')} key={`${choice.batch_id||'demo'}:${choice.id}`} aria-label={choice.text} aria-describedby={`reason-${choice.id}`} data-explanation={explained===choice.id} onPointerDown={e=>startPress(e,choice)} onPointerMove={movePress} onPointerUp={endPress} onPointerCancel={endPress} aria-pressed={selected===choice.id} disabled={busy||activityPaused} onClick={()=>choose(choice)}>{selected===choice.id&&<Diamond size={20} weight="fill"/>}<span className="choice-copy"><RichMessage text={choice.text}/></span><CaretRight className="choice-arrow" size={18} aria-hidden="true"/><span className="choice-tooltip" role="tooltip" id={`reason-${choice.id}`}><b>{choice.label}</b>{choice.hint}</span></button>)}</>:<div className="choice-empty">{activity.action==="ask_contact"?<ContactAssistPrompt name={contact?.name} busy={busy||choosingAssist||activityPaused} deferred={deferredContact===identity} onYes={()=>chooseAssistance(true)} onNo={()=>chooseAssistance(false)} onLater={()=>setDeferredContact(identity)} onReopen={()=>setDeferredContact(null)}/>:activity.animated?<ActivityIndicator label={activity.label}/>:<span>{activity.label}</span>}{state?.generation?.retained_closing&&<p className="retained-closing" aria-label="已发出的收尾">「{state.generation.retained_closing.text}」<small>你已发送</small></p>}{live&&activity.action==='retry_image'&&<button className="secondary" disabled={busy} onClick={()=>native('retry_latest_image',{contact_id:identity})}>再看一次</button>}{live&&activity.action==='generate'&&<button className="secondary" disabled={busy} onClick={()=>native('generate')}>再想一次</button>}{live&&state?.generation?.conversation_state==='wait'&&<button className="text-button" onClick={()=>setConfig(true)}>想开个新话题</button>}{live&&activity.action==='settings'&&<button className="secondary" onClick={()=>setConfig(true)}>{activity.actionLabel||"打开设置"}</button>}{live&&activity.resume&&<button className="secondary" disabled={busy} onClick={()=>native("generate")}>恢复生成</button>}{live&&activity.action==='attachment'&&lastHuman&&<button className="secondary" onClick={()=>openAttachment(lastHuman)}>补充附件内容</button>}</div>}{live&&lastHuman?.kind&&lastHuman.kind!=='text'&&choices.length>0&&<button className="attachment-shortcut text-button" onClick={()=>openAttachment(lastHuman)}>{lastHuman.kind==='image'?'查看图片':'查看附件'}</button>}</div>{!groupChat&&<PursuitProgress person={person} disabled={busy||!contact} onClick={()=>setModal('pursuit')}/>}</div>
 <section className="message-box" data-scene={scene.id} style={{'--dialogue-opacity':prefs.dialogue_opacity/100,'--scene-image':`url("${scene.image}")`}}><img data-role={role.id} className={'counterpart '+(role.id==='pending'?'pending-counterpart':role.id==='group-default'?'group-counterpart':'')} src={role.image} alt={`${contact?.name||'对方'}的${role.name}虚拟形象`}/><div className="nameplate" title={groupChat?contact?.name:undefined}>{dialogueSpeaker||state?.chat_display_name||'请选择聊天'}</div>{dateLabel&&<small className="message-date">{dateLabel} · 对方消息</small>}<p tabIndex={0} aria-label="对方最新台词" className={latest?.has_sticker?'has-sticker':''} style={{fontSize:(prefs.font_size+5)*1.3,'--message-size':prefs.font_size+'px'}}>{latest?.has_sticker?(sticker?<img className="sticker-image" src={sticker} alt="对方发送的原表情包"/>:<span className="sticker-placeholder">{stickerError||"正在读取表情包…"}</span>):<RichMessage text={dialogue}/>}</p></section>
 <footer className="window-footer"><button className="menu-button" onClick={()=>setModal('history')}><Clock size={24}/>历史</button><span className="footer-rule"/><span className="fill-status">{state?.mode==='demo'?'虚构演示':busy?'正在填入…':selected?<><CheckCircle size={16}/>已填入 · 未发送</>:'点击台词，直接填入'}</span><button className="menu-button" onClick={()=>setConfig(!config)} aria-expanded={config}><GearSix size={25} weight="fill"/>设置</button></footer>
 {showGuidance&&lastClicked.current&&<aside className="inline-guidance">{lastClicked.current.hint}</aside>}</>}
 </article>}
 {!desktop&&<><p className="prototype-note">{live?'已连接本机应用':'体验原型 · 虚构联系人与聊天'}</p>{!live&&<section className="preview-controls"><label>体验联系人<select aria-label="体验联系人" value={demoContact} onChange={e=>setDemoContact(e.target.value)}><option value="demo-xiaolin">小林</option><option value="demo-aning">阿宁</option></select></label><label className="check-label"><input type="checkbox" checked={previewMax} onChange={e=>setPreviewMax(e.target.checked)}/>模拟微信最大化</label></section>}{!live&&<section className="demo-input"><label htmlFor="wechat-draft">演示微信输入框</label><textarea id="wechat-draft" placeholder="点上面的任一台词，直接出现在这里" value={input} onChange={e=>setInput(e.target.value)}/><small>虚构体验，输入内容尚未发送。</small></section>}</>}
 </section>
 {config&&<SettingsPanel initialPage={activity.settingsPage||(activity.action==="settings"?"connection":"context")} prefs={prefs} state={state} contact={contact} person={person} role={role}
   rolePreviews={rolePreviews} roleNote={roleNote} roleCount={roles.length} scene={scene}
   busy={busy} saving={settingsSaving} saveError={settingsSaveError} live={live} desktop={desktop}
   adult={adult} groupChat={groupChat} onUpdate={update} onRange={changeRange} onPerson={setPerson}
   onRole={id=>{setCustom(p=>{const c={...p};delete c[identity];return c});setPerson({role:id})}}
   onModal={setModal} onClose={()=>setConfig(false)} onReset={resetPage} onNative={native} onSetup={startSetup}
   topicIntent={topicIntent} setTopicIntent={setTopicIntent}
   api={{endpoint,model,key:apiKey,type:apiType}} setApi={(field,value)=>({endpoint:setEndpoint,model:setModel,key:setApiKey,type:setApiType}[field])(value)}
   speech={{endpoint:asrEndpoint,model:asrModel,key:asrKey}} setSpeech={(field,value)=>({endpoint:setAsrEndpoint,model:setAsrModel,key:setAsrKey}[field])(value)}/>}</div>

 <dialog ref={dialogRef} className={'overlay-dialog '+(modal==='setup'?'setup-dialog':modal==='roles'?'role-library-dialog':'')} aria-labelledby="overlay-dialog-title" onCancel={()=>setModal(null)} onClose={()=>setModal(null)}><header><h2 id="overlay-dialog-title">{modal==='pursuit'?'聊天目标与阶段':modal==='attachment'?'附件内容':modal==='roles'?'对方角色库':modal==='examples'?'下一句，怎么接？':modal==='setup'?'框选微信聊天区域':`历史 · ${contact?.name||'当前聊天'}`}</h2><IconButton label={modal==='roles'?'关闭角色库':'关闭'} onClick={()=>setModal(null)}><X size={22}/></IconButton></header>
 {modal==='pursuit'&&!groupChat&&<PursuitSettings person={person} contact={contact} disabled={busy} onChange={setPerson} assessment={assessment} generationStatus={state?.generation?.status}/>}
 {modal==='attachment'&&live&&<AttachmentPanel key={attachment?.id} message={attachment} state={state} onState={serverPrefs} onNotice={setNotice}/>}
 {modal==='history'&&<><p className="help">{live?state?.history?.coverage:'虚构体验记录。选中的草稿尚未发送。'}</p>{messages.length>historyVisible&&<button className="secondary" onClick={()=>setHistoryVisible(n=>Math.min(messages.length,n+100))}>显示更早100条（当前{historyVisible}条）</button>}{messages.slice(-historyVisible).map((m,i)=><div className="history-entry" key={m.id||i}><span>{m.role==='system'?'系统事件':m.role==='self'?'你':groupChat?m.sender_display_name||'身份未确认':contact?.name} {m.timestamp||''}</span><p><RichMessage text={m.text}/></p>{live&&m.kind&&m.kind!=='text'&&m.kind!=='system'&&<button className="text-button" onClick={()=>openAttachment(m)}>查看／补充附件</button>}</div>)}{!live&&input&&<div className="history-entry"><span>你的草稿 · 未发送</span><p>{input}</p></div>}</>}
 {modal==='examples'&&<><p className="help">同一句“今天上班好累”，可以这样接。示例用于说明风格，实际回复结合当前聊天。</p>{examples.map(([label,text,hint])=><article className="reply-example" key={label}><h3>{label}</h3><p>{text}</p><small>{hint}</small></article>)}<p className="help">表情与颜文字：关闭时不用；自然时适量；活泼时更轻松。选项会跟随上下文变化。</p></>}
 {modal==='roles'&&<><div className="role-library-body"><p className="help">给{contact?.name||'对方'}选择虚拟形象。年龄是外观分类，不会自动决定台词。</p><div className="library-filters"><label>年龄段<select aria-label="角色年龄段" value={age} onChange={e=>setAge(e.target.value)}><option value="all">全部年龄</option>{ageBands.map(([id,range])=><option key={id} value={id}>{range}</option>)}</select></label><RadioOptions name="gender" value={gender} options={[['all','全部'],['male','男'],['female','女']]} onChange={setGender}/></div><div className="library-grid">{visibleRoles.map(r=><button className={role.id===r.id?'active':''} key={r.id} onClick={()=>{setCustom(p=>{const c={...p};delete c[identity];return c});setPerson({role:r.id});setModal(null)}}><img src={r.image} alt={r.name}/><span>{r.name}</span><small>{r.range}</small></button>)}</div><div className="custom-role"><h3><Plus size={16}/>添加自己的角色</h3><input aria-label="自定义角色名称" maxLength={16} value={customName} onChange={e=>setCustomName(e.target.value)} placeholder="角色名称"/><label className="upload-label">选择角色图片<input aria-label="自定义角色图片" type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>{const f=e.target.files?.[0];if(!f)return;if(!['image/png','image/jpeg','image/webp'].includes(f.type)||f.size>5*1024*1024){setNotice('请选择5MB以内的PNG、JPEG或WebP图片');return}if(custom[identity]?.image)URL.revokeObjectURL(custom[identity].image);setCustom(p=>({...p,[identity]:{id:'custom',name:customName.trim()||'自定义角色',image:URL.createObjectURL(f)}}));setModal(null)}}/></label><p className="help">自定义图片仅在本次界面中使用，不上传。</p></div></div><footer className="role-library-footer"><button className="primary" onClick={()=>setModal(null)}>返回设置</button></footer></>}
 {modal==='setup'&&setupImage&&<><p className="help">依次拖动框选：①完整聊天区（含标题、消息、输入框）②标题 ③输入框。</p><div className="calibration-image" ref={calibration} onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);dragStart.current=point(e)}} onPointerMove={e=>{if(dragStart.current)setDragBox([...dragStart.current,...point(e)])}} onPointerUp={finishBox}><img draggable="false" src={setupImage.image_data_url} alt="当前微信窗口，用于框选聊天区域"/>{[...setupRects,...(dragBox?[dragBox]:[])].map((r,i)=><div className="selection-rectangle" key={i} style={{left:Math.min(r[0],r[2])/setupImage.width*100+'%',top:Math.min(r[1],r[3])/setupImage.height*100+'%',width:Math.abs(r[2]-r[0])/setupImage.width*100+'%',height:Math.abs(r[3]-r[1])/setupImage.height*100+'%'}}><span>{i+1}</span></div>)}</div><label className="field-label">当前账号名称<input value={accountName} onChange={e=>setAccountName(e.target.value)}/></label><footer><button className="secondary" onClick={()=>setSetupRects([])}>重新框选</button><button className="primary" disabled={setupRects.length!==3||!accountName.trim()||busy} onClick={async()=>{const r=await native('visual_setup_save',{width:setupImage.width,height:setupImage.height,account_name:accountName,region:setupRects[0],title_region:setupRects[1],input_region:setupRects[2]});if(r){setModal(null);native('connect')}}}>保存区域</button></footer></>}
 </dialog>{notice&&<div className="toast" role="status">{notice}</div>}
 </main>
}



