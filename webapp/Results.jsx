import React,{useEffect,useState} from "react";

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

const sheetColumns=[
  ["case_number","Case number"],["cnr","CNR"],["case_status","Status"],
  ["petitioners","Petitioners"],["respondents","Respondents"],["case_type","Case type"],
  ["court_name","Court"],["court_number_and_judge","Judge"],["filing_number","Filing number"],
  ["filing_date_iso","Filed"],["registration_number","Registration number"],["registration_date_iso","Registered"],
  ["next_hearing_date_iso","Next hearing"],["decision_date_iso","Decision date"],["case_stage","Stage"],
  ["nature_of_disposal","Disposal"],["hearings","Hearings"],["orders","Orders"],["complete","Capture status"]
];
const listColumns=[sheetColumns[0],sheetColumns[1],...sheetColumns.slice(-3)];
const collator=new Intl.Collator(undefined,{numeric:true,sensitivity:"base"});
function cell(row,key){
  return key==="complete" ? (row.complete?"Complete":"Partial") : row[key]??"";
}

export default function Results({sheet,job}){
  const runs=useList("/api/results",job.ended);
  const [selected,setSelected]=useState(""),[query,setQuery]=useState(""),[page,setPage]=useState(0);
  const [sort,setSort]=useState({key:"cnr",direction:1});
  const current=job.directory?.split(/[\\/]/).pop();
  const options=runs.data||[];
  const run=selected||(sheet && current)||options[0]?.id;
  const exported=options.some(r=>r.id===run);
  const live=sheet && current && run===current;
  const url=run?"/api/results/"+encodeURIComponent(run)+(sheet?"/sheet":""):null;
  const cases=useList(url,live?[job.saved,job.state].join("/"):undefined);
  const columns=sheet?sheetColumns:listColumns;
  const term=query.trim().toLowerCase();
  const matches=(cases.data||[]).filter(row=>(sheet?columns:listColumns.slice(0,2)).some(([key])=>String(cell(row,key)).toLowerCase().includes(term)));
  if(sheet)matches.sort((a,b)=>sort.direction*collator.compare(cell(a,sort.key),cell(b,sort.key)));
  const visible=sheet?matches:matches.slice(page*25,(page+1)*25);
  function sortBy(key){
    setSort({key,direction:sort.key===key?-sort.direction:1});
  }
  return <main className="resultsPage"><div className="intro"><small>Capture archive</small><h1>{sheet?"Run spreadsheet":"Saved cases"}</h1></div><section className="card">
    <div className="grid"><label>Capture run<select value={run||""} onChange={e=>{setSelected(e.target.value);setPage(0);setQuery("");}}>
      {sheet && current && !options.some(r=>r.id===current) && <option value={current}>{current} (current run)</option>}
      {options.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
      <label>Find a case<input type="search" placeholder={sheet?"Search all columns":"CNR or case number"} value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/></label></div>
    {runs.error||cases.error?<p role="alert">Could not load saved cases. Refresh to retry.</p>:!runs.data?<p>Loading runs…</p>:!run?<p>Completed captures will appear here after export.</p>:!cases.data?<p>Loading cases…</p>:<>
      {exported && cases.data.some(row=>row.complete) && <SaveRun key={run} run={run}/>}
      <p>{matches.length} matching {matches.length===1?"case":"cases"}</p>
      <div className={sheet?"sheetScroll":"tableScroll"} role="region" aria-label={sheet?"Run spreadsheet":"Saved cases"} tabIndex={0}>
        <table className={sheet?"caseSheet":undefined}><thead><tr>{columns.map(([key,label])=>
          <th key={key} scope="col" aria-sort={sheet&&key===sort.key?(sort.direction===1?"ascending":"descending"):undefined}>
            {sheet?<button onClick={()=>sortBy(key)}>{label}{sort.key===key?(sort.direction===1?" ↑":" ↓"):""}</button>:label}</th>)}</tr></thead>
          <tbody>{visible.map(row=><tr key={row.cnr}>{columns.map(([key])=><td key={key}>{key==="case_number"&&exported?
            <a href={"/results/"+encodeURIComponent(run)+"/case/"+encodeURIComponent(row.cnr)} target="_blank" rel="noreferrer">{row.case_number||row.cnr}</a>:cell(row,key)}</td>)}</tr>)}</tbody></table></div>
      {!sheet&&matches.length>25&&<div className="pager"><button disabled={!page} onClick={()=>setPage(page-1)}>Previous</button><span>Page {page+1} of {Math.ceil(matches.length/25)}</span><button disabled={(page+1)*25>=matches.length} onClick={()=>setPage(page+1)}>Next</button></div>}
    </>}
  </section></main>;
}
