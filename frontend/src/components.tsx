import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine } from 'lucide-react';
import { useStore } from './store';
import { tr, trError, trDynamic, appLocale } from './i18n';
import { api, prefetchGroupRoute } from './api';

type InstallPromptEvent = Event & { prompt:()=>Promise<void>; userChoice:Promise<{outcome:'accepted'|'dismissed'}> };

export function LanguageToggle(){
  const {i18n,t}=useTranslation();
  const next=i18n.language==='en'?'hi':'en';
  return <button type="button" className="language-toggle" aria-label={t('language')}
    onClick={()=>{i18n.changeLanguage(next);localStorage.setItem('bb-lang',next);document.documentElement.lang=next}}>
    <span className="language-code">{i18n.language==='en'?'EN':'HI'}</span>
  </button>;
}

export function Logo({compact=false,src}:{compact?:boolean;src?:string|null}){
  const image=src||'/bharat-bachat-logo.png';
  return <div className={`brand ${compact?'brand-compact':''}`}>
    <img src={image} alt={tr("Bharat Bachat")} className={compact?'brand-mark brand-mark-lg':'brand-mark'} draggable={false} onError={e=>{e.currentTarget.src="/bharat-bachat-logo.png"}}/>
    {!compact&&<div className="brand-copy"><div className="brand-name">{tr("Bharat Bachat")}</div><div className="brand-tagline">{tr("Aapki Bachat, Aapka Vikas")}</div></div>}
  </div>;
}

const nav=[
  ['/dashboard','dashboard',Home],
  ['/members','members',Users],
  ['/register','register',BookOpen],
  ['/ledger','ledger',FileText],
  ['/analytics','analytics',BarChart3],
  ['/passbook','passbook',WalletCards],
  ['/loans','loans',CreditCard],
  ['/personal-loan','personalLoan',Receipt],
  ['/admin','admin',ShieldCheck],
] as const;


export function ThemeSettings({onDone}:{onDone?:()=>void}={}){
  const {i18n}=useTranslation(); const {tenant}=useStore();
  const setLanguage=()=>{const n=i18n.language==='en'?'hi':'en';i18n.changeLanguage(n);localStorage.setItem('bb-lang',n);document.documentElement.lang=n};
  return <div className="settings-panel">
    <div className="settings-row"><div><b>{tr('Language')}</b><span>{tr('Choose app language')}</span></div><button className="setting-pill" onClick={setLanguage}><span className="language-code">{i18n.language==='en'?'EN':'HI'}</span></button></div>
    <div className="settings-row settings-theme-row"><div><b>{tr('Theme')}</b><span>{tenant?.name||tr('Group')} {tr('visual system')}</span></div><div className="theme-choice active figma-theme-lock"><span className="theme-swatch" style={{background:'linear-gradient(135deg,#047857,#065F46)'}}/><span>{tenant?.name||tr('Group')}</span><Check size={14}/></div></div>
    {onDone&&<button type="button" className="btn-primary w-full mt-3" onClick={onDone}>{tr('Done')}</button>}
  </div>
}

export function Pagination({page,total,pageSize=5,onChange}:{page:number;total:number;pageSize?:number;onChange:(p:number)=>void}){const pages=Math.max(1,Math.ceil(total/pageSize));if(total<=pageSize)return null;return <div className="pagination"><button disabled={page<=1} onClick={()=>onChange(page-1)}>{tr('Previous')}</button>{Array.from({length:pages},(_,i)=>i+1).slice(Math.max(0,page-3),Math.min(pages,page+2)).map(p=><button key={p} className={p===page?'active':''} onClick={()=>onChange(p)}>{p}</button>)}<button disabled={page>=pages} onClick={()=>onChange(page+1)}>{tr('Next')}</button></div>}

export function SearchField({value,onChange,placeholder,ariaLabel}:{value:string;onChange:(value:string)=>void;placeholder:string;ariaLabel?:string}){return <div className="search-field-wrap"><input className="data-search" value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} aria-label={ariaLabel||placeholder}/>{value&&<button type="button" className="search-clear-btn" aria-label={tr('Clear')} onClick={()=>onChange('')}><X size={17}/></button>}</div>}

export function ActivityAccordion({items,onLoadMore,hasMore=false,loadingMore=false}:{items:{title:string;meta:string;value:string;tone?:'positive'|'negative'|'neutral';sortKey?:string}[];onLoadMore?:()=>void;hasMore?:boolean;loadingMore?:boolean}){
  const [open,setOpen]=useState(false); const [page,setPage]=useState(1); const pageSize=10; const sentinelRef=useRef<HTMLDivElement|null>(null);
  const sorted=useMemo(()=>items.slice().sort((a,b)=>String(b.sortKey||'').localeCompare(String(a.sortKey||''))),[items]);
  const pages=Math.max(1,Math.ceil(sorted.length/pageSize));
  useEffect(()=>{if(page>pages)setPage(1)},[pages,page]);
  useEffect(()=>{if(!open||!onLoadMore||!hasMore||loadingMore)return;const node=sentinelRef.current;if(!node)return;const observer=new IntersectionObserver(es=>{if(es[0]?.isIntersecting)onLoadMore()},{rootMargin:'350px 0px'});observer.observe(node);return()=>observer.disconnect()},[open,onLoadMore,hasMore,loadingMore,sorted.length]);
  const visible=onLoadMore?sorted:sorted.slice((page-1)*pageSize,page*pageSize);
  return <section className="card activity-accordion"><button className="activity-toggle" onClick={()=>setOpen(v=>!v)}><span><b>{tr('Latest Activity')}</b><small>{tr('View Activity')} · {sorted.length} {tr('entries')}</small></span><ChevronDown className={open?'rotated':''} size={19}/></button>{open&&<><div className="activity-list transaction-history-list">{visible.map((x,i)=>{const negative=x.tone==='negative';return <div className="activity-item gpay-activity-item" key={`${x.sortKey||''}-${i}`}><span className={`transaction-icon ${negative?'debit':'credit'}`}>{negative?<ArrowUpFromLine size={17}/>:<ArrowDownToLine size={17}/>}</span><div className="activity-main"><b>{x.title}</b><span>{x.meta}</span></div><strong className={`activity-amount ${negative?'negative':x.tone==='neutral'?'neutral':'positive'}`}>{x.value}</strong></div>})}{!sorted.length&&<div className="empty-state">{tr('No data yet')}</div>}</div>{onLoadMore?<div ref={sentinelRef} className="infinite-feed-sentinel">{loadingMore?tr('Loading more…'):hasMore?tr('Scroll for more'):''}</div>:pages>1&&<Pagination page={page} total={sorted.length} pageSize={pageSize} onChange={setPage}/>}</>}</section>}

function InstallButton(){
  const {t}=useTranslation();
  const [event,setEvent]=useState<InstallPromptEvent|null>(null);
  useEffect(()=>{
    const handler=(e:Event)=>{e.preventDefault();setEvent(e as InstallPromptEvent)};
    window.addEventListener('beforeinstallprompt',handler);
    return()=>window.removeEventListener('beforeinstallprompt',handler);
  },[]);
  if(!event) return null;
  return <button type="button" className="install-btn" onClick={async()=>{await event.prompt();setEvent(null)}}><Smartphone size={16}/>{t('installApp')}</button>;
}


export function InstallBanner(){const [event,setEvent]=useState<InstallPromptEvent|null>(null);const {t}=useTranslation();useEffect(()=>{const h=(e:Event)=>{e.preventDefault();setEvent(e as InstallPromptEvent)};window.addEventListener('beforeinstallprompt',h);return()=>window.removeEventListener('beforeinstallprompt',h)},[]);if(!event)return null;return <div className="install-banner"><img src="/pwa-192.png"/><div><b>{t('installApp')}</b><span>{tr('Install Bharat Bachat on this device')}</span></div><button onClick={async()=>{await event.prompt();setEvent(null)}}>{tr('Install')}</button></div>}
const mobileLabel=(key:string)=>({dashboard:'Home',members:'Members',register:'Cash Book',ledger:'Ledger',analytics:'Analytics',passbook:'Passbook',loans:'Loans',personalLoan:'My Loan',admin:'Admin',more:'More'} as Record<string,string>)[key]||key;

export function Layout({children}:{children:ReactNode}){
  const {t}=useTranslation();
  const {user,tenant,logout}=useStore();
  const loc=useLocation();
  const [more,setMore]=useState(false);
  const [profile,setProfile]=useState(false);
  const profileRef=useRef<HTMLDivElement|null>(null);
  const [settings,setSettings]=useState(false);
  const [unread,setUnread]=useState(0);
  const [notifications,setNotifications]=useState<any[]>([]); const [notifOpen,setNotifOpen]=useState(false); const [toast,setToast]=useState('');
  const refreshNotifications=async(openTray=false)=>{if(!user?.tenant_id)return;try{const rows=await api.notifications(user.tenant_id);const unreadRows=rows.filter((n:any)=>!n.read);setUnread(unreadRows.length);setNotifications(rows);if(openTray&&unreadRows.length){await Promise.all(unreadRows.map((n:any)=>api.markNotificationRead(user.tenant_id!,n._id).catch(()=>null)));setNotifications(rows.map((n:any)=>({...n,read:true})));setUnread(0)}}catch{}};
  const closeNotifications=async()=>{setNotifOpen(false);if(!user?.tenant_id||!notifications.length)return;const ids=notifications.map((n:any)=>n._id).filter(Boolean);try{await api.clearNotifications(user.tenant_id,ids);setNotifications([]);setUnread(0)}catch{}};
  const allowed=user?.role==='member'
    ? ['/dashboard','/analytics','/passbook','/loans','/personal-loan']
    : user?.role==='super_admin'
      ? ['/admin']
      : nav.map(x=>x[0]);
  const visible=nav.filter(x=>allowed.includes(x[0]));
  const primary=visible.slice(0,4);
  const hasMore=visible.length>4;
  const active=visible.find(x=>loc.pathname===x[0] || (x[0] !== '/dashboard' && loc.pathname.startsWith(`${x[0]}/`)));
  useEffect(()=>{setMore(false);setProfile(false)},[loc.pathname]);
  useEffect(()=>{const h=(e:Event)=>{const d=(e as CustomEvent).detail;setToast(String(d?.message||''));window.setTimeout(()=>setToast(''),2200)};window.addEventListener('bb-toast',h);return()=>window.removeEventListener('bb-toast',h)},[]);
  useEffect(()=>{
    if(!profile)return;
    const close=(event:PointerEvent)=>{const target=event.target as Node|null;if(profileRef.current&&!profileRef.current.contains(target))setProfile(false)};
    document.addEventListener('pointerdown',close,true);
    return()=>document.removeEventListener('pointerdown',close,true);
  },[profile]);
  useEffect(()=>{void refreshNotifications(false)},[user?.tenant_id]);
  const prefetch=(route:string)=>prefetchGroupRoute(user?.tenant_id||tenant?._id||'',route,user?.role,user?.member_id);
  useEffect(()=>{
    const tid=user?.tenant_id||tenant?._id||''; if(!tid)return;
    const run=()=>{prefetchGroupRoute(tid,'/analytics',user?.role,user?.member_id);prefetchGroupRoute(tid,'/loans',user?.role,user?.member_id);prefetchGroupRoute(tid,'/passbook',user?.role,user?.member_id)};
    const w=window as any; const id=w.requestIdleCallback? w.requestIdleCallback(run,{timeout:1800}):window.setTimeout(run,900);
    return()=>{if(w.cancelIdleCallback)w.cancelIdleCallback(id);else window.clearTimeout(id)};
  },[user?.tenant_id,user?.role,user?.member_id,tenant?._id]);

  return <div className="app-shell" data-route={loc.pathname}><InstallBanner/>
    <header className="app-header safe-top figma-header">
      <div className="app-header-inner">
        <Link to={user?.role==='super_admin'?'/admin':'/dashboard'} className="header-brand"><Logo compact/><span className="header-app-title">Bharat Bachat</span></Link>
        <div className="header-context"><span className="header-context-dot"/><span className="header-context-name">{tenant?.name||tr('Group Vault')}</span><span className="header-context-role">{user?.role==='super_admin'?tr('Super Admin'):user?.role==='group_admin'?tr('Group Admin'):tr('Member')}</span></div>
        <div className="header-actions">
          <InstallButton/>
          <LanguageToggle/>
          <button type="button" className="header-icon-btn" aria-label={tr('Notifications')} onClick={async()=>{await refreshNotifications(true);setNotifOpen(true)}}><Bell size={19}/>{unread>0&&<span>{unread>9?'9+':unread}</span>}</button>
          <div className="profile-wrap" ref={profileRef}>
            <button type="button" className="profile-trigger" onClick={()=>setProfile(v=>!v)} aria-expanded={profile}>
              <span className="avatar">{user?.profile_image_url?<img src={user.profile_image_url} alt=""/>:<UserCircle size={19}/>}</span>
              <span className="profile-name">{user?.name||user?.phone}</span>
              <ChevronDown size={15}/>
            </button>
            {profile&&<div className="profile-layer profile-dismiss-layer" onMouseDown={e=>{if(e.target===e.currentTarget)setProfile(false)}}><div className="profile-menu">
              <div className="profile-menu-head"><b>{user?.name||tr('Bharat Bachat')}</b><span>{user?.phone}</span></div>
              {user?.member_id&&<label className="profile-photo-action"><span>{tr('Profile Photo')}</span><input hidden type="file" accept="image/*" onChange={async e=>{const f=e.target.files?.[0];if(!f)return;try{const r=await api.uploadProfileImage(f);const st=useStore.getState();if(st.token){const fresh=await api.me().catch(()=>null);if(fresh)st.setAuth(st.token,fresh);else if(st.user)st.setAuth(st.token,{...st.user,profile_image_url:r.profile_image_url,profile_picture_url:r.profile_image_url})}setProfile(false)}catch{}finally{e.currentTarget.value=''}}}/></label>}
              <button type="button" className="profile-menu-neutral" onClick={async()=>{setProfile(false);await refreshNotifications(true);setNotifOpen(true)}}><Bell size={16}/>{tr('Notifications')}</button>
              <button type="button" className="profile-menu-neutral" onClick={()=>{setProfile(false);setSettings(true)}}><Settings size={16}/>{tr('Settings')}</button>
              <button type="button" onClick={()=>{setProfile(false);logout()}}><LogOut size={16}/>{t('logout')}</button>
            </div></div>}
          </div>
        </div>
      </div>
    </header>

    <div className="app-body">
      <aside className="desktop-sidebar figma-sidebar">
        <div className="sidebar-brand"><Logo compact src={tenant?.logo_url}/></div>
        <nav className="sidebar-nav">
          {visible.map(([to,label,Icon])=><Link key={to} to={to} onMouseEnter={()=>prefetch(to)} onTouchStart={()=>prefetch(to)} className={`sidebar-link ${loc.pathname===to || (to!=='/dashboard' && loc.pathname.startsWith(`${to}/`))?'is-active':''}`}><Icon size={19}/><span>{t(label)}</span></Link>)}
        </nav>
        <div className="sidebar-footer"><span>{user?.role==='super_admin'?tr('Super Admin'):user?.role==='group_admin'?tr('Group Admin'):tr('Member')}</span></div>
      </aside>

      <main className="app-main figma-main">
        <div className="page-content figma-page">{children}</div>
      </main>
    </div>

    <nav className="mobile-nav safe-bottom" aria-label={tr("Primary navigation")}>
      {primary.map(([to,label,Icon])=><Link key={to} to={to} onTouchStart={()=>prefetch(to)} className={`mobile-nav-item ${loc.pathname===to || (to!=='/dashboard' && loc.pathname.startsWith(`${to}/`))?'is-active':''}`}>{loc.pathname===to&&<motion.span layoutId="bb-nav-active" className="mobile-nav-active" transition={{type:'spring',stiffness:420,damping:30}}/>}<Icon size={19}/><span>{t(label)}</span></Link>)}
      {hasMore&&<button type="button" className={`mobile-nav-item ${active && !primary.some(x=>x[0]===active[0])?'is-active':''}`} onClick={()=>setMore(v=>!v)}><MoreHorizontal size={20}/><span>{t(mobileLabel('more'))}</span></button>}
    </nav>

    {notifOpen&&<div className="notification-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)closeNotifications()}}><div className="notification-panel"><div className="notification-head"><div><b>{tr('Notifications')}</b><span>{notifications.length} {tr('entries')}</span></div><button className="icon-btn" onClick={closeNotifications}><X size={18}/></button></div><div className="notification-list">{notifications.map((n:any)=><div className={`notification-item ${n.read?'read':''}`} key={n._id}><div><b>{trDynamic(n.title)}</b><p>{trDynamic(n.body)}</p><small>{n.created_at?new Date(n.created_at).toLocaleString(appLocale()):''}</small></div><button className="notification-delete" onClick={async()=>{try{if(user?.tenant_id)await api.deleteNotification(user.tenant_id,n._id);setNotifications(x=>x.filter(v=>v._id!==n._id));setUnread(x=>Math.max(0,x-(n.read?0:1)))}catch{}}}><X size={15}/></button></div>)}{!notifications.length&&<div className="empty-state">{tr('No data yet')}</div>}</div></div></div>}
    {settings&&<Modal title={tr('Settings')} onClose={()=>setSettings(false)}><ThemeSettings onDone={()=>setSettings(false)}/></Modal>}
    {toast&&<div className="bb-toast" role="status">{toast}</div>}
    {more&&<div className="mobile-more-overlay" onClick={()=>setMore(false)}>
      <div className="mobile-more-sheet safe-bottom" onClick={e=>e.stopPropagation()}>
        <div className="sheet-handle"/>
        <div className="sheet-head"><div><b>{t('more')}</b><span>{tenant?.name||''}</span></div><button type="button" className="icon-btn" onClick={()=>setMore(false)}><X size={19}/></button></div>
        <div className="more-grid">
          {visible.map(([to,label,Icon])=><Link key={to} to={to} onTouchStart={()=>prefetch(to)} onMouseEnter={()=>prefetch(to)} className={`more-item ${loc.pathname===to || (to!=='/dashboard' && loc.pathname.startsWith(`${to}/`))?'is-active':''}`}><Icon size={21}/><span>{t(label)}</span></Link>)}
        </div>
      </div>
    </div>}
  </div>;
}


export type ActionSheetItem = { label:string; onClick:()=>void; danger?:boolean; disabled?:boolean };

export function ActionSheet({title,subtitle,items,onClose}:{title:string;subtitle?:string;items:ActionSheetItem[];onClose:()=>void}){
  return <div className="action-sheet-backdrop" role="dialog" aria-modal="true" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div className="action-sheet" onMouseDown={e=>e.stopPropagation()}>
      <div className="sheet-handle"/>
      <div className="action-sheet-head"><div><h3>{title}</h3>{subtitle&&<p>{subtitle}</p>}</div><button type="button" className="icon-btn" onClick={onClose} aria-label={tr("Close")}><X size={18}/></button></div>
      <div className="action-sheet-list">
        {items.map((item,i)=><button key={`${item.label}-${i}`} type="button" disabled={item.disabled} className={`action-sheet-item ${item.danger?'is-danger':''}`} onClick={()=>{item.onClick();onClose()}}>{item.label}<span>›</span></button>)}
      </div>
    </div>
  </div>;
}

export function ListCard({children,className='',onClick}:{children:ReactNode;className?:string;onClick?:()=>void}){
  return <article className={`list-card figma-list-card ${className}`} onClick={onClick}>{children}</article>;
}

export function PageTitle({title,subtitle}:{title:string;subtitle?:string}){
  const {tenant}=useStore();
  const groupName=tenant?.name||tr('Group');
  return <div className="page-heading figma-page-heading">
    <div className="page-heading-copy"><span className="page-heading-kicker">{groupName}</span><h1>{title}</h1>{subtitle&&<p>{subtitle}</p>}</div>
  </div>;
}

export function StatCard({label,value,icon}:{label:string;value:string;icon:ReactNode}){
  const key=String(label).toLowerCase();
  const tone=/(inflow|credit|contribution|collected|income)/.test(key)?'metric-inflow':/(outflow|debit|expense|repayment|penalty)/.test(key)?'metric-outflow':'';
  return <div className={`stat-card figma-stat-card ${tone}`}>
    <div className="stat-copy"><span>{label}</span><strong>{value}</strong></div>
    <div className="stat-icon">{icon}</div>
  </div>;
}

export function Field({label,...props}:any){
  return <label className="form-field figma-field"><span>{label}</span><input className="input" {...props}/></label>;
}
export function SelectField({label,value,onChange,children,...props}:any){
  return <label className="form-field figma-field"><span>{label}</span><select className="input" value={value} onChange={onChange} {...props}>{children}</select></label>;
}
export function TextArea({label,...props}:any){
  return <label className="form-field figma-field"><span>{label}</span><textarea className="input textarea" {...props}/></label>;
}

export function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}){
  const content=<div className="modal-backdrop" role="dialog" aria-modal="true" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div className={`modal-card ${/Loan|loan|Request|request/.test(title)?'loan-modal':''}`}>
      <div className="modal-head"><h2>{title}</h2><button type="button" className="icon-btn" aria-label={tr("Close")} onClick={onClose}><X size={19}/></button></div>
      <div className="modal-body">{children}</div>
    </div>
  </div>;
  return typeof document==='undefined'?content:createPortal(content,document.body);
}

export function ErrorBox({error,onClose}:{error:string;onClose?:()=>void}){
  const [dismissed,setDismissed]=useState(false);
  useEffect(()=>setDismissed(false),[error]);
  if(!error||dismissed)return null;
  const close=()=>{setDismissed(true);onClose?.()};
  return <div className="error-banner" role="alert"><div className="error-content"><span className="error-dot"/><span>{trError(error)}</span></div><button type="button" className="error-close" aria-label={tr("Close")} onClick={close}><X size={17}/></button></div>;
}
