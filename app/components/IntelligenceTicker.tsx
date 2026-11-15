'use client';

import {useState} from 'react';
import {ArrowDownRight,ArrowRight,ArrowUpRight,ExternalLink,X} from 'lucide-react';

export type TickerAlert={
  id:string;
  company:string;
  ticker:string;
  headline:string;
  category:string;
  priority:'high'|'medium'|'low';
  movement?:number;
  timestamp?:string;
  sourceUrl?:string;
  explanation:string;
  status:'live'|'cached'|'sample'|'unavailable';
};

type Props={alerts:TickerAlert[];onOpenCompany:(alert:TickerAlert)=>void};

const priorityRank={high:0,medium:1,low:2};
const timeLabel=(value?:string)=>value?new Intl.DateTimeFormat('en-IN',{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'}).format(new Date(value)):'Time unavailable';

export default function IntelligenceTicker({alerts,onOpenCompany}:Props){
  const [selected,setSelected]=useState<TickerAlert|null>(null);
  const [touchPaused,setTouchPaused]=useState<number|null>(null);
  const ordered=[...alerts].sort((a,b)=>priorityRank[a.priority]-priorityRank[b.priority]||(b.timestamp||'').localeCompare(a.timestamp||''));

  const renderCard=(alert:TickerAlert,duplicate:boolean)=>{
    const Icon=alert.movement!=null&&alert.movement<0?ArrowDownRight:ArrowUpRight;
    return <button type="button" key={`${duplicate?'copy-':''}${alert.id}`} className={`ticker-card priority-${alert.priority}`} tabIndex={duplicate?-1:0} onClick={()=>setSelected(alert)} title={`${alert.company} — ${alert.headline}`}>
      <span className="ticker-symbol">{alert.ticker.slice(0,2)}</span>
      <span className="ticker-card-copy"><span className="ticker-company">{alert.company}<i>{alert.ticker}</i></span><span className="ticker-headline">{alert.headline}</span></span>
      {alert.movement!=null&&<span className={`ticker-movement ${alert.movement>=0?'positive':'negative'}`}><Icon size={13}/>{alert.movement>0?'+':''}{alert.movement.toFixed(1)}%</span>}
      <span className={`ticker-priority-dot ${alert.priority}`} title={`${alert.priority} priority`}/>
      {alert.status==='sample'&&<span className="ticker-sample">SAMPLE</span>}
    </button>;
  };

  const row=(items:TickerAlert[],index:number)=>{
    const rowItems=items.length?items:ordered;
    const cards=rowItems.length?rowItems:[];
    return <div className={`ticker-row ${index===1?'reverse':''} ${touchPaused===index?'touch-paused':''}`} key={index} onTouchStart={()=>setTouchPaused(index)} onTouchEnd={()=>setTouchPaused(null)} onTouchCancel={()=>setTouchPaused(null)}>
      <div className="ticker-track" style={{'--ticker-duration':`${index===0?35:42}s`} as React.CSSProperties}>
        <div className="ticker-track-set">{cards.map(alert=>renderCard(alert,false))}</div>
        <div className="ticker-track-set" aria-hidden="true">{cards.map(alert=>renderCard(alert,true))}</div>
      </div>
    </div>;
  };

  return <section className="intelligence-ticker" aria-label="High-priority intelligence ticker">
    <div className="ticker-heading"><div><span className="ticker-live-pulse"/><span>HIGH-PRIORITY INTELLIGENCE</span><small>{alerts.some(alert=>alert.status==='sample')&&alerts.every(alert=>alert.status==='sample')?'DEMO DATA':`${alerts.length} SIGNAL${alerts.length===1?'':'S'}`}</small></div><span className="ticker-hint">Select a card for details <ArrowRight size={12}/></span></div>
    {ordered.length?<div className="ticker-rows">{[0,1].map(index=>{const split=ordered.filter((_,cardIndex)=>cardIndex%2===index);return row(split.length?split:ordered.slice(0,Math.min(ordered.length,4)),index)})}</div>:<div className="ticker-empty">No high-priority intelligence is available yet.</div>}
    {selected&&<div className="ticker-modal-backdrop" onClick={()=>setSelected(null)}><section className="ticker-modal" role="dialog" aria-modal="true" aria-labelledby="ticker-modal-title" onClick={event=>event.stopPropagation()}>
      <button className="ticker-modal-close" type="button" onClick={()=>setSelected(null)} aria-label="Close alert details"><X size={17}/></button>
      <div className="ticker-modal-kicker"><span className={`ticker-priority-dot ${selected.priority}`}/>{selected.priority.toUpperCase()} PRIORITY · {selected.category.replaceAll('_',' ').toUpperCase()} <small>{selected.status.toUpperCase()}</small></div>
      <div className="ticker-modal-company"><span className="ticker-symbol large">{selected.ticker.slice(0,2)}</span><div><h2 id="ticker-modal-title">{selected.company}</h2><small>{selected.ticker} · {timeLabel(selected.timestamp)}</small></div></div>
      <h3 className="ticker-modal-headline">{selected.headline}</h3>
      {selected.movement!=null&&<div className={`ticker-modal-movement ${selected.movement>=0?'positive':'negative'}`}>{selected.movement>0?'+':''}{selected.movement.toFixed(2)}% daily movement</div>}
      <div className="ticker-modal-explanation"><b>WHY IT MATTERS</b><p>{selected.explanation||'No further explanation is available from the current source.'}</p></div>
      <div className="ticker-modal-actions">{selected.sourceUrl&&<a href={selected.sourceUrl} target="_blank" rel="noreferrer">Open source <ExternalLink size={13}/></a>}<button type="button" onClick={()=>{onOpenCompany(selected);setSelected(null)}}>Open company research <ArrowRight size={14}/></button></div>
    </section></div>}
  </section>;
}
