import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';

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


const mobileLabel=(key:string)=>({dashboard:'Dashboard',members:'Members',register:'Financial Register',ledger:'Member Ledger',analytics:'Analytics',passbook:'Passbook',loans:'Loans',personalLoan:'My Loan',admin:'Admin',more:'More'} as Record<string,string>)[key]||key;


import { Logo } from './Logo';
import { InstallBanner, InstallButton } from './InstallBanner';
import { Modal } from './Modal';
import { ThemeSettings } from './ThemeSettings';

export function Layout({children}:{children:ReactNode}){
  const {t}=useTranslation();
  const {user,tenant,logout}=useStore();
  const loc=useLocation();
  const [more,setMore]=useState(false);
  const [profile,setProfile]=useState(false);
  const profileRef=useRef<HTMLDivElement|null>(null);
  const [settings,setSettings]=useState(false);
  const [unread,setUnread]=useState(0);
  const [notifications,setNotifications]=useState<any[]>([]); const [notifOpen,setNotifOpen]=useState(false); const [toast,setToast]=useState<{message:string;type:'success'|'error'|'info'}|null>(null);
  const refreshNotifications=async(openTray=false)=>{if(!user?.tenant_id)return;try{const rows=await api.notifications(user.tenant_id);const unreadRows=rows.filter((n:any)=>!n.read);setUnread(unreadRows.length);setNotifications(rows);if(openTray&&unreadRows.length){await Promise.all(unreadRows.map((n:any)=>api.markNotificationRead(user.tenant_id!,n._id).catch(()=>null)));setNotifications(rows.map((n:any)=>({...n,read:true})));setUnread(0)}}catch{}};
  const closeNotifications=async()=>{setNotifOpen(false);if(!user?.tenant_id||!notifications.length)return;const ids=notifications.map((n:any)=>n._id).filter(Boolean);try{await api.clearNotifications(user.tenant_id,ids);setNotifications([]);setUnread(0)}catch{}};
  const allowed=user?.role==='member'
    ? ['/dashboard','/analytics','/passbook','/personal-loan','/loans']
    : user?.role==='super_admin'
      ? ['/admin']
      : nav.map(x=>x[0]);
  const visible=nav.filter(x=>allowed.includes(x[0]));
  // Group admins get a fixed, explicit mobile navigation order. Do not derive
  // this from the global nav array: adding a route must not silently reorder it.
  const adminPrimaryPaths=['/dashboard','/members','/register','/ledger'];
  const isGroupAdmin=['group_admin','admin'].includes(String(user?.role||''));
  const primary=isGroupAdmin
    ? visible.filter(x=>adminPrimaryPaths.includes(x[0])).sort((a,b)=>adminPrimaryPaths.indexOf(a[0])-adminPrimaryPaths.indexOf(b[0]))
    : user?.role==='member'
      ? visible.filter(x=>['/dashboard','/analytics','/passbook'].includes(x[0]))
      : visible.slice(0,4);
  const hasMore=true;
  // The More sheet is a secondary destination list, never a duplicate of the fixed footer tabs.
  const secondary=visible.filter(x=>!primary.some(p=>p[0]===x[0]));
  const active=visible.find(x=>loc.pathname===x[0] || (x[0] !== '/dashboard' && loc.pathname.startsWith(`${x[0]}/`)));
  useEffect(()=>{setMore(false);setProfile(false)},[loc.pathname,loc.search]);
  useEffect(()=>{let timer:number|undefined;const h=(e:Event)=>{const d=(e as CustomEvent).detail;window.clearTimeout(timer);setToast({message:String(d?.message||''),type:d?.type==='error'?'error':d?.type==='info'?'info':'success'});timer=window.setTimeout(()=>setToast(null),1000)};window.addEventListener('bb-toast',h);return()=>{window.clearTimeout(timer);window.removeEventListener('bb-toast',h)}},[]);
  useEffect(()=>{
    if(!profile)return;
    const close=(event:PointerEvent)=>{const target=event.target as Node|null;if(profileRef.current&&!profileRef.current.contains(target))setProfile(false)};
    document.addEventListener('pointerdown',close,true);
    return()=>document.removeEventListener('pointerdown',close,true);
  },[profile]);
  // Do not prefetch unrelated screens or fetch notifications on every route mount.
  // Each screen owns its required data calls; notifications load only when opened.

  return <div className="app-shell" data-route={loc.pathname}><InstallBanner/>
    <header className="app-header safe-top figma-header">
      <div className="app-header-inner">
        <Link to={user?.role==='super_admin'?'/admin':'/dashboard'} className="header-brand"><Logo compact/><span className="header-app-title flex-1 min-w-0 text-white font-bold">Bharat Bachat</span></Link>
        
        <div className="header-actions">
          <InstallButton/>
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
          {visible.map(([to,label,Icon])=><Link key={to} to={to} className={`sidebar-link ${loc.pathname===to || (to!=='/dashboard' && loc.pathname.startsWith(`${to}/`))?'is-active':''}`}><Icon size={19}/><span>{t(label)}</span></Link>)}
        </nav>
        <div className="sidebar-footer"><span>{user?.role==='super_admin'?tr('Super Admin'):user?.role==='group_admin'?tr('Group Admin'):tr('Member')}</span></div>
      </aside>

      <main className="app-main figma-main">
        <div className="page-content figma-page">{children}</div>
      </main>
    </div>

    <nav className="mobile-nav safe-bottom" aria-label={tr("Primary navigation")}>
      {primary.map(([to,label,Icon])=><Link key={to} to={to} className={`mobile-nav-item ${loc.pathname===to || (to!=='/dashboard' && loc.pathname.startsWith(`${to}/`))?'is-active':''}`}>{loc.pathname===to&&<motion.span layoutId="bb-nav-active" className="mobile-nav-active" transition={{type:'spring',stiffness:420,damping:30}}/>}<Icon size={19}/><span>{t(isGroupAdmin?mobileLabel(label):label)}</span></Link>)}
      {hasMore&&<button type="button" className={`mobile-nav-item ${active && !primary.some(x=>x[0]===active[0])?'is-active':''}`} onClick={()=>setMore(v=>!v)}><MoreHorizontal size={20}/><span>{t(mobileLabel('more'))}</span></button>}
    </nav>

    {notifOpen&&<div className="notification-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)closeNotifications()}}><div className="notification-panel"><div className="notification-head"><div><b>{tr('Notifications')}</b><span>{notifications.length} {tr('entries')}</span></div><button className="icon-btn" onClick={closeNotifications}><X size={18}/></button></div><div className="notification-list">{notifications.map((n:any)=><div className={`notification-item ${n.read?'read':''}`} key={n._id}><div><b>{trDynamic(n.title)}</b><p>{trDynamic(n.body)}</p><small>{n.created_at?new Date(n.created_at).toLocaleString(appLocale()):''}</small></div><button className="notification-delete" onClick={async()=>{try{if(user?.tenant_id)await api.deleteNotification(user.tenant_id,n._id);setNotifications(x=>x.filter(v=>v._id!==n._id));setUnread(x=>Math.max(0,x-(n.read?0:1)))}catch{}}}><X size={15}/></button></div>)}{!notifications.length&&<div className="empty-state">{tr('No data yet')}</div>}</div></div></div>}
    {settings&&<Modal title={tr('Settings')} onClose={()=>setSettings(false)}><ThemeSettings onDone={()=>setSettings(false)}/></Modal>}
    {toast&&<div className={`bb-toast bb-toast-${toast.type}`} role={toast.type==='error'?'alert':'status'} aria-live={toast.type==='error'?'assertive':'polite'}><span className="bb-toast-icon" aria-hidden="true">{toast.type==='success'?'✓':toast.type==='error'?'!':'i'}</span><span>{toast.message}</span></div>}
    {more&&<div className="mobile-more-overlay" onClick={()=>setMore(false)}>
      <div className="mobile-more-sheet safe-bottom" onClick={e=>e.stopPropagation()}>
        <div className="sheet-handle"/>
        <div className="sheet-head"><div><b>{t('more')}</b><span>{tenant?.name||''}</span></div><button type="button" className="icon-btn" onClick={()=>setMore(false)}><X size={19}/></button></div>
        <div className="more-grid">
          {secondary.map(([to,label,Icon])=><Link key={to} to={to} className={`more-item ${loc.pathname===to || (to!=='/dashboard' && loc.pathname.startsWith(`${to}/`))?'is-active':''}`}><Icon size={21}/><span>{t(label)}</span></Link>)}<Link to="/user-guide" className={`more-item ${loc.pathname==='/user-guide'?'is-active':''}`}><BookOpen size={21}/><span>{tr('App User Guide')}</span></Link><Link to="/formulas" className={`more-item ${loc.pathname==='/formulas'?'is-active':''}`}><FileText size={21}/><span>{tr('Formulas')}</span></Link>{['group_admin','admin'].includes(String(user?.role||''))&&<Link to="/admin?tab=settings" className={`more-item ${loc.pathname==='/admin'&&new URLSearchParams(loc.search).get('tab')==='settings'?'is-active':''}`}><Settings size={21}/><span>{tr('Loan & Kist Settings')}</span></Link>}{!['group_admin','admin'].includes(String(user?.role||''))&&<button type="button" className="more-item" onClick={()=>{setMore(false);setSettings(true)}}><Settings size={21}/><span>{tr('Settings')}</span></button>}
        </div>
      </div>
    </div>}
  </div>;
}
