import {useState} from 'react';
import {mutate,type WorkOutcome} from './api';

export function OutcomeActions({outcome}: {outcome:WorkOutcome}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[evidence,setEvidence]=useState('');
  if(['fulfilled','cancelled'].includes(outcome.status))return null;
  async function act(operation:string,extra:Record<string,unknown>={}) {
    setBusy(true);setError('');
    try {await mutate(`/api/v1/outcomes/${outcome.id}/${operation}`,'POST',{expected_revision:outcome.revision,...extra});}
    catch(err){setError((err as Error).message);}
    finally{setBusy(false);}
  }
  return <div className="outcome-actions">
    {outcome.status==='uncertain'&&outcome.kind==='native_effect'?<>
      <label>Evidence of what happened<textarea rows={2} value={evidence} onChange={e=>setEvidence(e.target.value)} maxLength={2000} disabled={busy}/></label>
      <div className="button-row"><button className="secondary" disabled={busy||evidence.trim().length<10} onClick={()=>void act('reconcile',{result:'fulfilled',evidence})}>Confirmed completed</button><button className="secondary" disabled={busy||evidence.trim().length<10} onClick={()=>void act('reconcile',{result:'failed',evidence})}>Confirmed not completed</button></div>
    </>:outcome.status!=='uncertain'&&<button className="inspect-work" disabled={busy} onClick={()=>void act('drop')}>Remove this requirement</button>}
    {error&&<p role="alert" className="task-error">{error}</p>}
  </div>;
}
