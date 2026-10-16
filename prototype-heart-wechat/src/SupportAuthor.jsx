import {useRef,useState} from 'react';
import {ArrowSquareOut,Globe,GithubLogo,Heart,X} from '@phosphor-icons/react';
import {AUTHOR_LINKS} from './authorLinks';

export function SupportAuthor(){
  const dialog=useRef(null);
  const [missing,setMissing]=useState(false);
  return <div className="author-support">
    <div className="support-intro"><Heart weight="duotone" size={34}/><div>
      <h3>给心动一点鼓励</h3><p>喜欢这个小小的提词器？请作者喝杯奶茶吧。</p>
    </div></div>
    <section className="support-card" aria-label="支付宝赞助">
      <button className="support-code" aria-label="放大支付宝收款码" disabled={missing} onClick={()=>dialog.current.showModal()}>
        {!missing?<img src={AUTHOR_LINKS.sponsorImage} alt="作者的支付宝收款码" onError={()=>setMissing(true)}/>:<span>收款码暂时无法显示</span>}
      </button>
      <div className="support-copy"><span className="support-eyebrow">THANK YOU ♥</span><h4>支付宝扫码</h4>
        <p>点击图片可放大<br/>金额随心，感谢支持。</p><small>赞助完全自愿<br/>不影响功能使用</small></div>
    </section>
    <nav className="support-links" aria-label="作者链接">
      <a href={AUTHOR_LINKS.github} target="_blank" rel="noopener noreferrer"><GithubLogo size={23}/><span>GitHub 项目<small>源码、反馈与更新</small></span><ArrowSquareOut size={17}/></a>
      <a href={AUTHOR_LINKS.website} target="_blank" rel="noopener noreferrer"><Globe size={23}/><span>{AUTHOR_LINKS.websiteLabel}<small>网站准备中 · 端口 10001</small></span><ArrowSquareOut size={17}/></a>
    </nav>
    <dialog ref={dialog} className="support-qr-dialog" aria-label="支付宝收款码大图" onClick={e=>{if(e.target===e.currentTarget)dialog.current.close()}}>
      <header><span>支付宝 · 自愿赞助</span><button aria-label="关闭收款码大图" onClick={()=>dialog.current.close()}><X size={21}/></button></header>
      <img src={AUTHOR_LINKS.sponsorImage} alt="作者支付宝收款码大图"/>
      <button className="support-qr-back" onClick={()=>dialog.current.close()}>返回赞助页</button>
    </dialog>
  </div>;
}
