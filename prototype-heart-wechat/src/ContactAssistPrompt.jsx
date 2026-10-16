export function ContactAssistPrompt({name,busy,deferred,onYes,onNo,onLater,onReopen}) {
 if(deferred)return <div className="contact-assist-deferred"><p>这次先不辅助，等你准备好。</p>
  <button className="text-button" onClick={onReopen}>选择是否辅助</button></div>;
 return <section className="contact-assist-prompt" aria-label="选择聊天辅助">
  <span className="contact-assist-eyebrow">故事，要继续吗？</span>
  <h3><span className="contact-assist-name" title={name}>「{name}」</span>要开启辅助聊天吗？</h3>
  <p>选一次就记住，以后不用再选。</p>
  <div className="contact-assist-actions"><button className="primary" disabled={busy} onClick={onYes}>是，开启辅助</button>
   <button className="secondary" disabled={busy} onClick={onNo}>不需要</button></div>
  <button className="contact-assist-later text-button" disabled={busy} onClick={onLater}>稍后决定</button>
 </section>;
}
