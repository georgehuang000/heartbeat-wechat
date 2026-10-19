export function ContactAssistPrompt({name,busy,deferred,decided=false,onYes,onNo,onLater,onReopen}) {
 if(deferred)return <div className="contact-assist-deferred"><p>这次先不辅助，等你准备好。</p>
  <button className="text-button" onClick={onReopen}>选择是否辅助</button></div>;
 return <section className="contact-assist-prompt" aria-label="选择聊天辅助">
  <span className="contact-assist-eyebrow">当前聊天</span>
  <h3><span className="contact-assist-name" title={name}>「{name}」</span></h3>
  <p>已在本地识别当前聊天。<br/>开启后，将必要聊天内容提交给你配置的模型。</p>
  <div className="contact-assist-actions"><button className="primary" disabled={busy} onClick={onYes}>{busy?'正在开启…':'开启此聊天'}</button>
   {!decided&&<button className="secondary" disabled={busy} onClick={onNo}>不需要</button>}</div>
  {!decided&&<button className="contact-assist-later text-button" disabled={busy} onClick={onLater}>稍后决定</button>}
 </section>;
}
