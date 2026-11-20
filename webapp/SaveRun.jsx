import React, {useState} from "react";

export default function SaveRun({run}) {
  const [state,setState]=useState("ready");
  const [message,setMessage]=useState("");
  async function save() {
    setState("saving");
    setMessage("");
    try {
      const response=await fetch("/api/results/"+encodeURIComponent(run)+"/save", {
        method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"
      });
      const result=await response.json();
      if (!response.ok) throw Error(result.error);
      setState("done");
      setMessage(result.written
        ? result.written+" case "+(result.written===1?"record":"records")+" added or updated in the shared database."
        : "The shared database is up to date for this run.");
    } catch (error) {
      setState("error");
      setMessage(error.message || "Save failed. Please retry.");
    }
  }
  return <div className="saveRun">
    <button type="button" onClick={save} disabled={state==="saving"||state==="done"}>
      {state==="saving"?"Saving…":state==="done"?"Saved to DB":"Save to DB"}
    </button>
    {message && <p role={state==="error"?"alert":"status"}>{message}</p>}
  </div>;
}
