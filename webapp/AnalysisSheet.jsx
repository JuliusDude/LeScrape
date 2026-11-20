import React,{useState} from "react";
const sheetColumns=[
  ["case_number","Case number"],["cnr","CNR"],["case_status","Status"],
  ["petitioners","Petitioners"],["respondents","Respondents"],["case_type","Case type"],
  ["court_name","Court"],["court_number_and_judge","Judge"],["filing_number","Filing number"],
  ["filing_date_iso","Filed"],["registration_number","Registration number"],["registration_date_iso","Registered"],
  ["next_hearing_date_iso","Next hearing"],["decision_date_iso","Decision date"],["case_stage","Stage"],
  ["nature_of_disposal","Disposal"],["hearings","Hearings"],["orders","Orders"],["complete","Capture status"],["capture_date","Captured"]
];

const collator=new Intl.Collator(undefined,{numeric:true,sensitivity:"base"});
function cell(row,key){
  return key==="complete" ? (row.complete?"Complete":"Partial") : row[key]??"";
}

const dims={court_name:"Court",case_type:"Case type",case_status:"Status",case_stage:"Stage",year:"Filing year"};
const metrics={cases:"Cases",hearings:"Hearings",orders:"Orders"};
const charts={decision:"Time to decision",pending:"Pending-case age",hearings:"Hearings per case",orders:"Orders per case"};
const emptyFilters={court_name:"",case_type:"",case_status:"",from:"",to:""};
const dayBands=[[0,89],[90,179],[180,364],[365,729],[730,1824],[1825,Infinity]];
const countBands=[[0,0],[1,2],[3,5],[6,10],[11,20],[21,Infinity]];
function group(row,key){
  return (key==="year"?row.filing_date_iso?.slice(0,4):row[key]) || "Unrecorded";
}
function measure(row,key){
  if(key==="hearings"||key==="orders")return row[key];
  const status=(row.case_status||"").toLowerCase();
  if(key==="decision"?!/\bdisposed\b/.test(status):!/\bpending\b/.test(status))return null;
  const end=key==="decision"?row.decision_date_iso:row.capture_date;
  const days=(Date.parse(end)-Date.parse(row.filing_date_iso))/86400000;
  return Number.isInteger(days)&&days>=0?days:null;
}
function selectedBy(row,drill){
  if(!drill)return true;
  if(drill.kind==="pivot")return group(row,drill.x)===drill.a && group(row,drill.y)===drill.b;
  const value=measure(row,drill.metric);
  return value!=null && value>=drill.low && (drill.high==null||value<=drill.high);
}
function Pick({label,value,options,onChange}){
  return <label>{label}<select aria-label={label} value={value} onChange={e=>onChange(e.target.value)}>{options.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>;
}
function Pivot({rows,onDrill}){
  const [x,setX]=useState("court_name"),[y,setY]=useState("case_status"),[metric,setMetric]=useState("cases");
  const groups=new Map(),columns=new Set();
  for(const row of rows){
    const a=group(row,x),b=group(row,y);
    if(!groups.has(a))groups.set(a,new Map());
    const cells=groups.get(a);
    cells.set(b,(cells.get(b)??0)+(metric==="cases"?1:row[metric]));
    columns.add(b);
  }
  const headers=[...columns].sort(collator.compare);
  return <><div className="analysisGrid"><Pick label="Rows" value={x} options={Object.entries(dims)} onChange={v=>{setX(v);if(v===y)setY(x);}}/>
    <Pick label="Columns" value={y} options={Object.entries(dims).filter(([k])=>k!==x)} onChange={setY}/>
    <Pick label="Measure" value={metric} options={Object.entries(metrics)} onChange={setMetric}/></div>
    <div className="pivotScroll"><table><caption>{metrics[metric]} by {dims[x].toLowerCase()} and {dims[y].toLowerCase()}</caption><thead><tr><th>{dims[x]}</th>{headers.map(b=><th key={b}>{b}</th>)}</tr></thead><tbody>
      {[...groups.keys()].sort(collator.compare).map(a=><tr key={a}><th>{a}</th>{headers.map(b=><td key={b}>{groups.get(a).has(b)?<button onClick={()=>onDrill({kind:"pivot",x,y,a,b,label:a+" · "+b})}>{groups.get(a).get(b)}</button>:"—"}</td>)}</tr>)}</tbody></table></div></>;
}
function Distribution({rows,metric,onDrill}){
  const days=metric==="decision"||metric==="pending";
  const values=rows.map(row=>measure(row,metric)).filter(v=>v!=null).sort((a,b)=>a-b);
  const bands=(days?dayBands:countBands).map(([low,high])=>({low,high,n:values.filter(v=>v>=low&&v<=high).length}));
  const median=values.length?(values[Math.floor((values.length-1)/2)]+values[Math.floor(values.length/2)])/2:null;
  return <><p>{metric==="decision"?"Filing to decision for disposed cases.":metric==="pending"?"Filing to capture date for pending cases.":"Recorded "+metric+" per case."} {values.length} of {rows.length} cases contribute.</p>
    {median!=null&&<p><strong>Median · {median} {days?"days":metric}</strong></p>}
    {bands.map(({low,high,n})=>{
      const label=(high===Infinity?low+"+":low===high?low:low+"–"+high)+" "+(days?"days":metric);
      return <button className="chartBand" key={low} disabled={!n} onClick={()=>onDrill({kind:"band",metric,low,high:high===Infinity?null:high,label:charts[metric]+" · "+label})}>
        <span>{label}</span><i aria-hidden="true" style={{width:(100*n/Math.max(1,...bands.map(b=>b.n)))+"%"}}/><b>{n}</b></button>;
    })}</>;
}
function download(run,rows,cols){
  function csv(value){
    let s=String(value??"");
    if(/^[\s]*[=+@-]/.test(s))s="'"+s;
    return '"'+s.replaceAll('"','""')+'"';
  }
  const fields=[["run","Capture run"],["capture_date","Captured"],...cols.filter(([k])=>k!=="capture_date")];
  const lines=[fields.map(([,label])=>label),...rows.map(row=>fields.map(([key])=>key==="run"?run:cell(row,key)))];
  const blob=new Blob(["\ufeff",lines.map(line=>line.map(csv).join(",")).join("\r\n")],{type:"text/csv;charset=utf-8"});
  const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=run+"-filtered.csv";link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
}
export default function AnalysisSheet({rows,run,exported,query,setQuery}){
  const [open,setOpen]=useState(false),[tab,setTab]=useState("pivot");
  const [filters,setFilters]=useState(emptyFilters),[sort,setSort]=useState({key:"cnr",direction:1});
  const [hidden,setHidden]=useState([]),[drill,setDrill]=useState(null);
  const [name,setName]=useState(""),[message,setMessage]=useState("");
  const storage="lescrape.sheet.views.v1."+run;
  const [saved,setSaved]=useState(()=>{try{const data=JSON.parse(localStorage.getItem(storage));return Array.isArray(data)?data:[];}catch{return [];}});
  const cols=sheetColumns.filter(([key])=>!hidden.includes(key));
  const term=query.trim().toLowerCase();
  const base=rows.filter(row=>["court_name","case_type","case_status"].every(key=>!filters[key]||group(row,key)===filters[key])
    && (!filters.from||row.filing_date_iso>=filters.from) && (!filters.to||(row.filing_date_iso&&row.filing_date_iso<=filters.to))
    && (!term||sheetColumns.some(([k])=>String(cell(row,k)).toLowerCase().includes(term))));
  const visible=base.filter(row=>selectedBy(row,drill)).sort((a,b)=>sort.direction*collator.compare(cell(a,sort.key),cell(b,sort.key)));
  function update(key,value){setFilters({...filters,[key]:value});setDrill(null);}
  function save(){
    const title=name.trim();if(!title)return;
    const views=[...saved.filter(v=>v.name!==title),{name:title,filters,sort,query,hidden,drill}];
    try{localStorage.setItem(storage,JSON.stringify(views));setSaved(views);setMessage("View saved in this browser.");}catch{setMessage("Browser storage unavailable.");}
  }
  function restore(title){
    const v=saved.find(v=>v.name===title);if(!v)return;
    setName(title);setFilters(v.filters);setQuery(v.query);setHidden(v.hidden);setDrill(v.drill);setSort(v.sort);setMessage("View restored.");
  }
  return <><div className="analysisTools"><button aria-expanded={open} onClick={()=>setOpen(!open)}>{open?"Close analysis":"Analyze"}</button>
    <button disabled={!visible.length} onClick={()=>download(run,visible,cols)}>Export filtered CSV</button>
    <span>{visible.length} of {rows.length} captured cases</span></div>
    {drill&&<button className="drillChip" onClick={()=>setDrill(null)} aria-label={"Clear selection: "+drill.label}>{drill.label} ×</button>}
    <div className={open?"analysisLayout":""}>{open&&<aside className="analysisPanel" aria-label="Spreadsheet analysis">
      <h2>Analyze cases</h2><details open><summary>Filters</summary>
      {["court_name","case_type","case_status"].map(key=><Pick key={key} label={dims[key]} value={filters[key]} options={[["","All"],...[...new Set(rows.map(r=>group(r,key)))].sort(collator.compare).map(v=>[v,v])]} onChange={v=>update(key,v)}/>)}
      <div className="analysisGrid">{["from","to"].map(key=><label key={key}>Filed {key}<input type="date" min={key==="to"?filters.from:undefined} max={key==="from"?filters.to:undefined} value={filters[key]} onChange={e=>update(key,e.target.value)}/></label>)}</div>
      <button onClick={()=>{setFilters(emptyFilters);setQuery("");setDrill(null);}}>Clear filters</button></details>
      <Pick label="Analysis" value={tab} options={[["pivot","Pivot table"],...Object.entries(charts)]} onChange={v=>{setTab(v);setDrill(null);}}/>
      <p>{base.length} of {rows.length} captured cases in analysis</p>
      {tab==="pivot"?<Pivot rows={base} onDrill={setDrill}/>:<Distribution rows={base} metric={tab} onDrill={setDrill}/>}
      <details><summary>Columns and saved views</summary>
      {sheetColumns.map(([key,label])=><label className="colCheck" key={key}><input type="checkbox" checked={!hidden.includes(key)} disabled={key==="cnr"} onChange={()=>setHidden(hidden.includes(key)?hidden.filter(k=>k!==key):[...hidden,key])}/>{label}</label>)}
      <label>View name<input value={name} onChange={e=>setName(e.target.value)}/></label><button onClick={save} disabled={!name.trim()}>Save view</button>
      <Pick label="Restore view" value="" options={[["","Choose saved view"],...saved.map(v=>[v.name,v.name])]} onChange={restore}/>
      {message&&<p role="status">{message}</p>}</details>
    </aside>}
    <div className="sheetScroll" role="region" aria-label="Run spreadsheet" tabIndex={0}><table className="caseSheet"><thead><tr>{cols.map(([key,label])=>
      <th key={key} scope="col" aria-sort={sort.key===key?(sort.direction===1?"ascending":"descending"):undefined}><button onClick={()=>setSort({key,direction:sort.key===key?-sort.direction:1})}>{label}{sort.key===key?(sort.direction===1?" ↑":" ↓"):""}</button></th>)}</tr></thead>
    <tbody>{visible.map(row=><tr key={row.cnr}>{cols.map(([key])=><td key={key}>{key==="case_number"&&exported?<a href={"/results/"+encodeURIComponent(run)+"/case/"+row.cnr} target="_blank" rel="noreferrer">{row.case_number||row.cnr}</a>:cell(row,key)}</td>)}</tr>)}</tbody></table>
    {!visible.length&&<p>No matching cases.</p>}</div></div></>;
}
