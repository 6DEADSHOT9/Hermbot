import {useState} from 'react';
import {Circle} from 'lucide-react';
import {mutate, type InputRequest} from './api';
import {useQueryClient} from '@tanstack/react-query';

export function InputCard({request,actionable=true}: {request:InputRequest;actionable?:boolean}) {
  const query=useQueryClient();
  const [answer,setAnswer]=useState('');
  const [busy,setBusy]=useState(false);
  const [sent,setSent]=useState(false);
  const [error,setError]=useState('');
  async function send(value:string) {
    if(busy||!value.trim())return;
    setBusy(true);setError('');
    try {
      await mutate(`/api/v1/input-requests/${request.id}/answer`,'POST',{answer:value,expected_question_hash:request.question_hash});
      setSent(true);
      void query.invalidateQueries({queryKey:['timeline']});
      void query.invalidateQueries({queryKey:['state']});
    } catch(err) {setError((err as Error).message);setBusy(false);}
  }
  return <section className={`review-card input-card ${!actionable||request.status!=='pending'?'is-closed':''}`} data-input-request-id={request.id}>
    <div className="review-title"><Circle size={18}/><strong>{request.status==='answered'?'Question answered':actionable?'Your answer is needed':`Question · ${request.status==='pending'?'no longer active':request.status}`}</strong></div>
    <p>{request.prompt}</p>
    {request.status==='answered'?<p className="question-receipt">Answer recorded · {request.answer}</p>:!actionable?<p className="question-receipt">This question is closed.</p>:request.answer_type==='text'?<form onSubmit={event=>{event.preventDefault();void send(answer);}}>
      <label>Answer<textarea aria-label="Answer the bot's question" value={answer} onChange={event=>setAnswer(event.target.value)} maxLength={5000} rows={3} disabled={busy} required/></label>
      <button className="primary" disabled={busy||!answer.trim()}>{sent?'Answer sent · continuing':busy?'Sending answer…':'Send answer'}</button>
    </form>:<div className="button-row">{request.choices.map(choice=><button key={choice} className="secondary" disabled={busy} onClick={()=>void send(choice)}>{choice}</button>)}</div>}
    {error&&<p className="task-error" role="alert">{error}</p>}
  </section>;
}
