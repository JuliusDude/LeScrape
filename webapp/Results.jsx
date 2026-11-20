import React,{useEffect,useState} from "react";

import AnalysisSheet from "./AnalysisSheet";
import SaveRun from "./SaveRun";

function useList(url,revision){
  const [result,setResult]=useState({});
  useEffect(()=>{
    if(!url)return;
    const abort=new AbortController();
    fetch(url,{signal:abort.signal}).then(response=>{
      if(!response.ok)throw Error();
      return response.json();
    }).then(data=>setResult({url,data})).catch(()=>{if(!abort.signal.aborted)setResult({url,error:true});});
    return ()=>abort.abort();
  },[url,revision]);
  return result.url===url?result:{};
}

const listColumns=[["case_number","Case number"],["cnr","CNR"],["hearings","Hearings"],["orders","Orders"],["complete","Capture status"]];

export default function Results({sheet,job}){
  const runs=useList("/api/results",job.ended);
  const [selected,setSelected]=useState(""),[query,setQuery]=useState(""),[page,setPage]=useState(0);
  const current=job.directory?.split(/[\\/]/).pop();
  const options=runs.data||[];
  const run=selected||(sheet && current)||options[0]?.id;
  const exported=options.some(r=>r.id===run);
  const live=sheet && current && run===current;
  const url=run?"/api/results/"+encodeURIComponent(run)+(sheet?"/sheet":""):null;
  const cases=useList(url,live?[job.saved,job.state].join("/"):undefined);
  const term=query.trim().toLowerCase();
  const matches=(cases.data||[]).filter(row=>[row.cnr,row.case_number].some(v=>String(v??"").toLowerCase().includes(term)));
  return <main className="resultsPage"><div className="intro"><small>Capture archive</small><h1>{sheet?"Run spreadsheet":"Saved cases"}</h1></div><section className="card">
    <div className="grid"><label>Capture run<select value={run||""} onChange={e=>{setSelected(e.target.value);setPage(0);setQuery("");}}>
      {sheet && current && !options.some(r=>r.id===current) && <option value={current}>{current} (current run)</option>}
      {options.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
      <label>Find a case<input type="search" placeholder={sheet?"Search all columns":"CNR or case number"} value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/></label></div>
    {runs.error||cases.error?<p role="alert">Could not load saved cases. Refresh to retry.</p>:!runs.data?<p>Loading runs…</p>:!run?<p>Completed captures will appear here after export.</p>:!cases.data?<p>Loading cases…</p>:<>
      {exported && cases.data.some(row=>row.complete) && <SaveRun key={run} run={run}/>}
      {sheet?<AnalysisSheet key={run} rows={cases.data} run={run} exported={exported} query={query} setQuery={setQuery}/>:<>
      <p>{matches.length} matching {matches.length===1?"case":"cases"}</p>
      <div className="tableScroll" role="region" aria-label="Saved cases" tabIndex={0}><table><thead><tr>
        {listColumns.map(([key,label])=><th key={key} scope="col">{label}</th>)}</tr></thead>
        <tbody>{matches.slice(page*25,(page+1)*25).map(row=><tr key={row.cnr}>{listColumns.map(([key])=>
          <td key={key}>{key==="case_number"?<a href={"/results/"+encodeURIComponent(run)+"/case/"+row.cnr} target="_blank" rel="noreferrer">{row.case_number||row.cnr}</a>:key==="complete"?(row.complete?"Complete":"Partial"):row[key]}</td>)}</tr>)}</tbody></table></div>
      {matches.length>25&&<div className="pager"><button disabled={!page} onClick={()=>setPage(page-1)}>Previous</button><span>Page {page+1} of {Math.ceil(matches.length/25)}</span><button disabled={(page+1)*25>=matches.length} onClick={()=>setPage(page+1)}>Next</button></div>}</>}
    </>}
  </section></main>;
}
