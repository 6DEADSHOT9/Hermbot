import {useEffect, useRef, useState} from 'react';
import {useQuery, useQueryClient} from '@tanstack/react-query';
import {api, type Approval, type Artifact, type Attachment, type InputRequest, type Message, type Task} from './api';

export type TimelineEntry = {
  id:string; position:number; revision:number; occurred_at:string; thread_id:number|null; deleted?:boolean;
  message?:Message; tasks:Task[]; question?:InputRequest; review?:Approval; actionable?:boolean;
  text?:string; author?:string; status?:string; task_id?:string; attempt_id?:string;
  origin?:string; cause_message_id?:number|null; cause_question_id?:string; legacy_answer?:string;
};
export type Timeline = {entries:TimelineEntry[];cursor:number;has_earlier:boolean;has_more:boolean;artifacts:Artifact[];attachments:Attachment[];actions?:Record<string,boolean>;mode?:'older'|'catchup';versions?:Record<string,number>;deferred?:Record<string,TimelineEntry>};

export function mergeTimeline(previous:Timeline|undefined, incoming:Timeline):Timeline {
  if(!previous)return {...incoming,versions:Object.fromEntries(incoming.entries.map(e=>[e.id,e.revision])),entries:incoming.entries.filter(e=>!e.deleted).sort((a,b)=>a.position-b.position)};
  const entries=new Map(previous.entries.map(entry=>[entry.id,entry]));
  const versions={...previous.versions};
  const deferred={...previous.deferred};
  const lower=previous.entries[0]?.position??0;
  for(const update of incoming.entries){
    const entry=deferred[update.id]&&deferred[update.id].revision>update.revision?deferred[update.id]:update;
    const existing=entries.get(entry.id);
    if((versions[entry.id]??existing?.revision??-1)>entry.revision)continue;
    versions[entry.id]=entry.revision;
    if(entry.deleted){entries.delete(entry.id);delete deferred[entry.id];continue;}
    if(incoming.mode==='catchup'&&!existing&&entry.position<lower){deferred[entry.id]=entry;continue;}
    entries.set(entry.id,entry);
    delete deferred[entry.id];
  }
  const actions=incoming.mode==='older'?previous.actions:incoming.actions;
  return {...incoming,versions,deferred,actions,entries:[...entries.values()].map(entry=>actions&&entry.id in actions?{...entry,actionable:actions[entry.id]}:entry).sort((a,b)=>a.position-b.position),
    artifacts:incoming.mode==='older'?previous.artifacts:incoming.artifacts,
    attachments:incoming.mode==='older'?previous.attachments:incoming.attachments,
    cursor:incoming.mode==='older'?previous.cursor:Math.max(previous.cursor,incoming.cursor),
    has_earlier:incoming.mode==='catchup'?previous.has_earlier:incoming.has_earlier};
}

export function useTimeline(conversation:string,thread:number|undefined,enabled:boolean){
  const query=useQueryClient();
  const key=['timeline',conversation,thread??null];
  const path=`/api/v1/conversations/${conversation}/timeline`;
  const [loadingEarlier,setLoadingEarlier]=useState(false);
  const olderRequest=useRef<AbortController|null>(null);
  useEffect(()=>{setLoadingEarlier(false);return()=>olderRequest.current?.abort();},[conversation,thread]);
  const result=useQuery({queryKey:key,enabled:!!conversation&&enabled,refetchInterval:5000,
    structuralSharing:(old,next)=>mergeTimeline(old as Timeline|undefined,next as Timeline),
    queryFn:async({signal})=>{
      const previous=query.getQueryData<Timeline>(key);
      if(!previous)return api<Timeline>(`${path}${thread?`?thread=${thread}`:''}`,{signal});
      let cursor=previous.cursor;
      let next:Timeline={...previous,entries:[],mode:'catchup'};
      do{
        const page=await api<Timeline>(`${path}?after=${cursor}${thread?`&thread=${thread}`:''}`,{signal});
        if(page.has_more&&page.cursor<=cursor)throw new Error('Conversation refresh did not advance. Please retry.');
        next={...page,entries:[...next.entries,...page.entries],mode:'catchup'};
        cursor=page.cursor;
      }while(next.has_more);
      return next;
    }});
  async function earlier(beforeMerge?:()=>void){
    const current=query.getQueryData<Timeline>(key);
    if(!current?.entries.length||olderRequest.current)return;
    const controller=new AbortController();olderRequest.current=controller;setLoadingEarlier(true);
    try{
      const page=await api<Timeline>(`${path}?before=${current.entries[0].position}${thread?`&thread=${thread}`:''}`,{signal:controller.signal});
      if(!controller.signal.aborted){beforeMerge?.();query.setQueryData<Timeline>(key,previous=>mergeTimeline(previous,{...page,mode:'older'}));}
    }finally{if(olderRequest.current===controller){olderRequest.current=null;setLoadingEarlier(false);}}
  }
  return {...result,earlier,loadingEarlier};
}
