import React, { Component, type ErrorInfo, type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, useLocation } from "react-router-dom";
import { registerSW } from "virtual:pwa-register";
import "./i18n";
import "./styles.css";
import App from "./App";

registerSW({ immediate: true });
document.documentElement.lang = localStorage.getItem("bb-lang") || "en-IN";

function ScrollToTop(){
  const location=useLocation();
  React.useEffect(()=>{
    window.scrollTo({top:0,left:0,behavior:"auto"});
    document.querySelectorAll("[data-scroll-container]").forEach((el:any)=>{el.scrollTop=0;el.scrollLeft=0;});
  },[location.pathname,location.search]);
  return null;
}

class AppErrorBoundary extends Component<{children:ReactNode},{hasError:boolean}> {
  state={hasError:false};
  static getDerivedStateFromError(){ return {hasError:true}; }
  componentDidCatch(error:Error, info:ErrorInfo){ console.error("Bharat Bachat UI error", error, info); }
  render(){
    if(this.state.hasError) return <main style={{minHeight:"100dvh",display:"grid",placeItems:"center",padding:24,fontFamily:"Inter,system-ui,sans-serif",background:"#f6faf8"}}><section style={{width:"min(100%,460px)",padding:24,borderRadius:24,background:"#fff",border:"1px solid #dbe7e1",boxShadow:"0 12px 40px rgba(26,43,76,.08)",textAlign:"center"}}><h1 style={{margin:0,color:"#1A2B4C",fontSize:22}}>Bharat Bachat</h1><p style={{color:"#64748b"}}>Something went wrong. Please reload the app.</p><button style={{minHeight:48,border:0,borderRadius:14,padding:"0 18px",background:"#0F9D58",color:"#fff",fontWeight:800}} onClick={()=>location.reload()}>Reload</button></section></main>;
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(<BrowserRouter><ScrollToTop/><AppErrorBoundary><App /></AppErrorBoundary></BrowserRouter>);
