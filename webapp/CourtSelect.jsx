import React, {useEffect, useState} from "react";

export default function CourtSelect({level,label,query,value,onChange,enabled=true}){
  const [result,setResult]=useState(null);
  const [attempt,setAttempt]=useState(0);
  const url="/api/options/"+level+"?"+new URLSearchParams(query);
  useEffect(()=>{
    if(!enabled)return;
    const controller=new AbortController();
    async function load(){
      try{
        const response=await fetch(url,{signal:controller.signal});
        if(!response.ok)throw Error();
        const options=await response.json();
        setResult({url,options});
      }catch{
        if(!controller.signal.aborted)setResult({url,error:true});
      }
    }
    load();
    return ()=>controller.abort();
  },[url,enabled,attempt]);
  const current=enabled&&result?.url===url?result:null;
  const options=current?.options||[];
  const placeholder=!enabled?"Select the preceding field":current?.error?"Unable to load options":!current?"Loading…":options.length?"Select "+label.toLowerCase():"No options available";
  return <div><label>{label}<select required disabled={!options.length} value={value} onChange={e=>onChange(e.target.value)}>
    <option value="">{placeholder}</option>{options.map(([id,name])=><option key={id} value={id}>{name}</option>)}
  </select></label>{current?.error&&<button type="button" onClick={()=>setAttempt(n=>n+1)}>Retry</button>}</div>;
}
