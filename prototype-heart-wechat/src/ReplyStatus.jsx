import {ActivityIndicator} from './ActivityIndicator';

export function ReplyStatus({activity,detail,busy,onUpdate,onSettings}) {
  if(!activity.label&&!activity.action)return null;
  return <div className="reply-status-bar" role="status" title={detail||undefined}>
    {activity.animated?<ActivityIndicator compact label={activity.label}/>:<span>{activity.label}</span>}
    {activity.action==='generate'&&<button className="text-button" disabled={busy} onClick={onUpdate}>更新回复</button>}
    {activity.action==='settings'&&<button className="text-button" onClick={onSettings}>{activity.actionLabel||'打开设置'}</button>}
    {activity.resume&&<button className="text-button" disabled={busy} onClick={onUpdate}>恢复生成</button>}
  </div>;
}
