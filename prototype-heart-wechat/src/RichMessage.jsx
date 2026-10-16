import emojiMap from './wechatEmojiMap.json';

export function RichMessage({text}) {
 const parts=String(text||'').split(/(\[[^\[\]\n]{1,24}\])/g);
 return parts.map((part,index)=>emojiMap[part]
  ? <img className="native-emoji" key={index} src={emojiMap[part]} alt={part} title={part}/>
  : <span key={index}>{part}</span>);
}
