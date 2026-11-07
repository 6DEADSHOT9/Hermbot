const labels:Record<string,string>={channel:'Slack recipient or conversation',text:'Message',to:'Recipient',subject:'Subject',body:'Content',calendar_id:'Calendar',summary:'Title',start:'Starts',end:'Ends',description:'Description',attendees:'Attendees',folder_id:'Drive folder',name:'Name',content:'Content',repo:'Repository',number:'Issue or pull request',title:'Title',head:'Source branch',base:'Target branch',path:'File',branch:'Branch',message:'Commit message',thread_ts:'Slack thread',event_id:'Event',state:'State',sha:'Current file version'};

export function ConnectorPreview({action,recipient}: {action:Record<string,unknown>;recipient?:string}) {
  const name=String(action.action||'');
  const args=(action.arguments||{}) as Record<string,unknown>;
  return <div className="connector-preview"><strong>{name==='slack.post'?'Send Slack message':name.replaceAll('.',' · ').replaceAll('_',' ')}</strong>
    <dl>{Object.entries(args).filter(([,value])=>value!==''&&value!==false&&value!=null).map(([key,value])=><div key={key}><dt>{labels[key]||key.replaceAll('_',' ')}</dt><dd>{key==='channel'&&recipient?`${recipient} (${String(value)})`:typeof value==='object'?JSON.stringify(value):String(value)}</dd></div>)}</dl>
  </div>;
}

export function ConnectorDraftFields({value,onChange}: {value:string;onChange:(value:string)=>void}) {
  let action:Record<string,unknown>;
  try {action=JSON.parse(value);}catch{return <label>Exact draft<textarea rows={12} value={value} onChange={e=>onChange(e.target.value)}/></label>;}
  const args=action.arguments as Record<string,unknown>|undefined;
  if(!action.action||!args)return <label>Exact draft<textarea aria-label="Review draft" rows={12} value={value} onChange={e=>onChange(e.target.value)}/></label>;
  function change(key:string,next:unknown){onChange(JSON.stringify({...action,arguments:{...args,[key]:next}}));}
  return <>{Object.entries(args).map(([key,current])=><label key={key}>{labels[key]||key.replaceAll('_',' ')}{
    typeof current==='boolean'?<input aria-label={labels[key]||key.replaceAll('_',' ')} type="checkbox" checked={current} onChange={e=>change(key,e.target.checked)}/>:
    typeof current==='number'?<input aria-label={labels[key]||key.replaceAll('_',' ')} type="number" value={current} onChange={e=>change(key,Number(e.target.value))}/>:
    ['text','body','content','description'].includes(key)||Array.isArray(current)?<textarea aria-label={labels[key]||key.replaceAll('_',' ')} rows={5} value={Array.isArray(current)?current.join('\n'):String(current)} onChange={e=>change(key,Array.isArray(current)?e.target.value.split('\n').map(v=>v.trim()).filter(Boolean):e.target.value)}/>:
    <input aria-label={labels[key]||key.replaceAll('_',' ')} value={String(current)} onChange={e=>change(key,e.target.value)}/>
  }</label>)}</>;
}
