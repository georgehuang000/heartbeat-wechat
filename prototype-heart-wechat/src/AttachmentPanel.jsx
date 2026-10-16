import {useState} from 'react';
import {call} from './bridge';
import {attachmentCoverage,attachmentNames} from './attachmentCoverage';

const names=attachmentNames;
export function AttachmentPanel({message,state,onState,onNotice}){
 const [preview,setPreview]=useState(null),[text,setText]=useState(''),[busy,setBusy]=useState(false);
 if(!message)return <p>当前没有附件。</p>;
 message=state.messages?.find(m=>m.id===message.id)||message;
 const payload={id:message.id,contact_id:state.contact.id,revision:state.revision};
 async function run(method,extra={}){setBusy(true);try{const r=await call(method,{...payload,...extra});if(r.state)onState(r.state);onNotice(r.message||'已完成');if(r.data_url)setPreview(r)}catch(e){onNotice(e.message)}finally{setBusy(false)}}
 const visual=message.kind==='image'||message.kind==='emoji';
 const cloud=visual||message.kind==='voice';
 const readingVoice=state.attachment_status?.kind==='voice'&&state.attachment_status?.id===message.id&&state.attachment_status?.status==='processing';
 return <section className="attachment-panel">
  <p className="attachment-heading">{names[message.kind]||'附件'} · {message.timestamp||'发送时间未知'}</p>
  <p className="help">{attachmentCoverage(message)}</p>
  {message.attachment?.description&&<p className="attachment-body">{message.attachment.description}</p>}
  {message.attachment?.address&&<p className="attachment-body">分享地点：{message.attachment.place_name} · {message.attachment.address}</p>}
  {message.attachment?.author&&<p className="help">卡片作者：{message.attachment.author}（不代表当前聊天的人）</p>}
  {message.attachment?.items?.map((item,i)=><div className="history-entry" key={i}><span>引用：{item.author||'原作者未知'} · {item.timestamp||'原时间未知'}</span><p>{item.text}</p></div>)}
  {visual&&<><button className="secondary" disabled={busy} onClick={()=>run('attachment_preview')}>查看原图</button>{preview&&<figure><img className="attachment-preview" src={preview.data_url} alt="当前消息的图片"/><figcaption>{preview.message}</figcaption></figure>}</>}
  {message.content_known&&<p className="attachment-body">{message.text}</p>}
  {message.kind==='voice'&&<><button className="primary" disabled={busy||readingVoice} onClick={()=>run(message.content_source==='wechat_transcription'?'refresh_voice_text':'transcribe_voice')}>{readingVoice?'正在读这条语音…':message.content_source==='wechat_transcription'?'重新读取微信转文字':'用微信转文字'}</button><p className="help">让这条语音和前后消息一起显示在微信里，即可转文字，无需配置备用语音API。</p></>}
  {cloud&&<div className="cloud-notice"><p>{visual?`将这张图加入本次回复，与已选择的图片和双方聊天一起发送到 ${state.settings.base_url}（${state.settings.model}）。`:`本次仅将这条语音发送到 ${state.settings.asr_base_url}（${state.settings.asr_model}）转写，结果会加入聊天上下文。`}</p><p className="help">{state.settings.media_policy==='off'?'云端附件处理已关闭，可在设置开启。':message.kind==='voice'&&!state.settings.asr_ready?'备用语音API未配置；可粘贴微信转文字结果。':'原图未缓存时会提示；普通缓存、缩略图和动图显示可能不完整。'}</p><button className="primary" disabled={busy||readingVoice||state.settings.media_policy==='off'||(message.kind==='voice'&&!state.settings.asr_ready)} onClick={()=>run('analyze_attachment',{consent:true})}>{busy||readingVoice?'正在处理…':visual?'传图生成回复':'使用备用语音API'}</button></div>}
  <label className="field-label">补充这条附件的内容<textarea aria-label="附件补充内容" value={text} onChange={e=>setText(e.target.value)} maxLength={12000} placeholder="粘贴转文字结果，或描述你希望讨论的图片、视频内容"/></label><button className="secondary" disabled={busy||!text.trim()} onClick={()=>run('supply_attachment_text',{text})}>加入当前上下文</button>
 </section>
}
