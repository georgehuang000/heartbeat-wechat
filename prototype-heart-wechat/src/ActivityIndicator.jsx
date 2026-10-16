export function ActivityIndicator({label='正在想下一句…',compact=false,detail}){
 return <span className={`thinking-indicator${compact?' compact':''}`} role="status" aria-live="polite" title={detail}>
  <span className="thinking-dots" aria-hidden="true"><i/><i/><i/></span><span>{label}</span>
 </span>;
}
