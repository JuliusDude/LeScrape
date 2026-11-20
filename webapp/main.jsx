import React,{useEffect,useState} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";
import Results from "./Results";
import SaveRun from "./SaveRun";
import CourtSelect from "./CourtSelect";

const defaults={state:"",district:"",complex:"",establishment:"",type:"",complexValue:"",flag:"Y",year:"2023",status:"Disposed",limit:"100"};
const clock=s=>[Math.floor(s/3600),Math.floor(s/60)%60,s%60].map(n=>String(n).padStart(2,"0")).join(":");

function describeIssue(event){
  if(event.stage==="Portal connection" && event.message.includes("RemoteDisconnected")){
    const attempt=event.message.match(/\(attempt (\d+)\)/)?.[1];
    return "The portal closed the connection before sending a response. V2 will retry this request."+
      (attempt?" Attempt "+attempt+" for this request.":"");
  }
  return event.message;
}

function currentView(){
  return ({"#results":"results","#sheet":"sheet"})[location.hash] || "capture";
}

function App(){
  const [view,setView]=useState(currentView);
  useEffect(()=>{const change=()=>setView(currentView());window.addEventListener("hashchange",change);return ()=>window.removeEventListener("hashchange",change);},[]);
  const [form,setForm]=useState(defaults),[job,setJob]=useState({state:"Connecting"}),[answer,setAnswer]=useState(""),[error,setError]=useState(""),[offline,setOffline]=useState(false),[sending,setSending]=useState(false);
  useEffect(()=>{let mounted=true,timer; async function poll(){try{const r=await fetch("/api/status",{cache:"no-store"});if(!r.ok)throw Error();const next=await r.json();if(mounted){setJob(next);setOffline(false);}}catch{if(mounted)setOffline(true);}finally{if(mounted)timer=setTimeout(poll,1000);}}poll();return()=>{mounted=false;clearTimeout(timer);};},[]);
  useEffect(()=>setAnswer(""),[job.challenge]);
  const [limit,setLimit]=useState("");
  useEffect(()=>{if(job.state==="Choose limit")setLimit(String(Math.min(100,job.available)));},[job.selection]);
  const idle=["Ready","Complete","Partial","Failed"].includes(job.state);
  async function post(path,payload){setSending(true);setError("");try{const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(!r.ok)throw Error((await r.json().catch(()=>({}))).error||"Request was rejected. Check the current run status.");const updated=await fetch("/api/status");setJob(await updated.json());}catch(e){setError(e.message);}finally{setSending(false);}}
  function field(key,label,options){const props={value:form[key],onChange:e=>setForm({...form,[key]:e.target.value}),required:true};return <label>{label}{options?<select {...props}>{options.map(o=><option key={o}>{o}</option>)}</select>:<input {...props} pattern={key==="type"?"[0-9]+(\\^[0-9]+)?":"[0-9]+"} inputMode={key==="type"?"text":"numeric"}/>}</label>;}
  const hierarchy=["state","district","complex","establishment","type"];
  function court(level,label){
    const index=hierarchy.indexOf(level);
    const parents=hierarchy.slice(0,index);
    const query=Object.fromEntries(parents.map(k=>[k,form[k]]));
    const enabled=parents.every(k=>form[k] || (k==="establishment" && form.flag==="N"));
    function choose(value){
      const next={...form};
      for(const child of hierarchy.slice(index+1))next[child]="";
      next[level]=value;
      if(index<=2){
        next.complexValue=level==="complex"?value:"";
        const [code,,flag]=next.complexValue.split("@");
        next.complex=code;
        next.flag=flag||"Y";
      }
      setForm(next);
    }
    if(level==="establishment" && form.flag==="N")return null;
    return <CourtSelect {...{level,label,query,enabled}} value={level==="complex"?form.complexValue:form[level]} onChange={choose}/>;
  }
  const elapsed=job.started?Math.max(0,Math.floor((job.ended||Date.now()/1000)-job.started)):0;
  function configure(){
    location.hash="capture";
    requestAnimationFrame(()=>requestAnimationFrame(()=>document.getElementById("configuration")?.scrollIntoView()));
  }
  return <><header className="siteHead"><div className="headInner"><a href="#capture" className="brand">LeScrape<i aria-hidden="true"/></a><nav className="appNav"><a href="#capture" aria-current={view==="capture"?"page":undefined}>Capture</a><a href="#results" aria-current={view==="results"?"page":undefined}>Saved cases</a><a href="#sheet" aria-current={view==="sheet"?"page":undefined}>Spreadsheet</a></nav><button className="headerCta" onClick={configure}>Configure run ↗</button></div></header>{view!=="capture"?<Results sheet={view==="sheet"} job={job}/>:<main className="capturePage">

  <div className="layout"><form id="configuration" className="card" onSubmit={e=>{e.preventDefault();post("/api/run",form);}}><small>Capture settings</small><h2>Run configuration</h2><fieldset disabled={!idle||sending||offline}><legend>Court jurisdiction</legend><div className="grid">{court("state","State")}{court("district","District")}{court("complex","Court complex")}{court("establishment","Establishment")}</div></fieldset><fieldset disabled={!idle||sending||offline}><legend>Case selection</legend><div className="grid">{court("type","Case type")}{field("year","Filing year")}{field("status","Case status",["Disposed","Pending","Both"])}</div></fieldset><button disabled={!idle||sending||offline||!form.type}>Find matching cases</button></form>
  <section className="monitor"><div className="monitorHead"><small>LIVE MONITOR</small><strong role="status">{offline?"Disconnected":job.state}</strong></div><h2>Run progress</h2><div className="count">{job.saved||0}<span> / {job.summary?.scheduled??job.limit??job.available??"—"}</span></div><p>Cases saved</p><progress value={job.saved||0} max={job.summary?.scheduled||job.limit||Number(form.limit)||1}/><dl><div><dt>Elapsed time</dt><dd>{clock(elapsed)}</dd></div><div><dt>Complete cases</dt><dd>{job.complete||0}</dd></div><div><dt>Request ceiling</dt><dd>1 / sec</dd></div></dl>
  {offline&&<p role="alert">Connection to the dashboard was lost. Showing the last known status and retrying automatically.</p>}
  {job.progressError&&<p role="alert">{job.progressError}</p>}
  {job.events?.length>0&&<section className="issues" aria-label="Scraping issues"><h3>Run issues and recoveries</h3>
    {job.state==="Complete"&&<p><strong>Capture finished. All {job.complete} saved cases are complete.</strong></p>}
    <p role="status">{job.eventCount} events · Latest {job.events.length} shown</p><ol>{job.events.slice().reverse().map(event=><li key={event.id}>
    <strong>{event.level} · {event.stage}</strong> <time>{new Date(event.at*1000).toLocaleTimeString()}</time>
    {event.cnr&&<div>CNR {event.cnr}</div>}<div>{describeIssue(event)}</div>{describeIssue(event)!==event.message&&<details><summary>Technical details</summary>{event.message}</details>}</li>)}</ol></section>}
  {job.available!=null&&<p>{job.available.toLocaleString()} matching cases available</p>}
  {job.state==="Choose limit"&&<form className="captcha" onSubmit={e=>{e.preventDefault();post("/api/limit",{selection:job.selection,limit:Number(limit)});}}>
    <label>Cases to capture (1–{job.available.toLocaleString()})<input type="number" required min="1" max={job.available} step="1" value={limit} onChange={e=>setLimit(e.target.value)}/></label>
    <button disabled={sending||offline}>Run scraper</button>
  </form>}
  {job.state==="Awaiting CAPTCHA"&&<form className="captcha" onSubmit={e=>{e.preventDefault();post("/api/captcha",{challenge:job.challenge,text:answer});}}><label>Enter the portal CAPTCHA<img alt="Portal CAPTCHA" src={"data:image/png;base64,"+job.image}/><input required pattern="[A-Za-z0-9]{6}" maxLength="6" value={answer} onChange={e=>setAnswer(e.target.value)}/></label><button disabled={sending}>Submit answer</button></form>}
  {["Partial","Complete"].includes(job.state)&&job.complete>0&&<SaveRun key={job.directory} run={job.directory.split(/[\\/]/).pop()}/>}
  {["Partial","Complete"].includes(job.state)&&<div className="downloads">{["cases.csv","cases.xlsx","hearings.csv","orders.csv"].map(name=><a href={"/download/"+name} key={name}>{name}</a>)}</div>}{job.directory&&<a className="sheetLink" href="#sheet">View run spreadsheet</a>}{job.directory&&<p className="path">{job.directory}</p>}</section></div>{(error||job.error)&&<p role="alert">{error||job.error}</p>}
  <section className="metrics" aria-label="Current run statistics">{[
    ["Cases available",job.available??"—"],["Cases captured",job.saved??"—"],["Complete cases",job.complete??"—"],
    ["Elapsed time",clock(elapsed)],["Request ceiling","1 / sec"]
  ].map(([label,value])=><div key={label}><small>{label}</small><strong>{value}</strong></div>)}</section>
  </main>}</>;
}
createRoot(document.getElementById("root")).render(<App/>);
