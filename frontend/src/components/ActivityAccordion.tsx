import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';
import { Pagination } from './Pagination';


export function ActivityAccordion({items,onLoadMore,hasMore=false,loadingMore=false}:{items:{title:string;meta:string;value:string;tone?:'positive'|'negative'|'neutral';sortKey?:string}[];onLoadMore?:()=>void;hasMore?:boolean;loadingMore?:boolean}){
  const [open,setOpen]=useState(false); const [page,setPage]=useState(1); const pageSize=10; const sentinelRef=useRef<HTMLDivElement|null>(null);
  const sorted=useMemo(()=>items.slice().sort((a,b)=>String(b.sortKey||'').localeCompare(String(a.sortKey||''))),[items]);
  const pages=Math.max(1,Math.ceil(sorted.length/pageSize));
  useEffect(()=>{if(page>pages)setPage(1)},[pages,page]);
  useEffect(()=>{if(!open||!onLoadMore||!hasMore||loadingMore)return;const node=sentinelRef.current;if(!node)return;const observer=new IntersectionObserver(es=>{if(es[0]?.isIntersecting)onLoadMore()},{rootMargin:'350px 0px'});observer.observe(node);return()=>observer.disconnect()},[open,onLoadMore,hasMore,loadingMore,sorted.length]);
  const visible=onLoadMore?sorted:sorted.slice((page-1)*pageSize,page*pageSize);
  return <section className="card activity-accordion"><button className="activity-toggle" onClick={()=>setOpen(v=>!v)}><span><b>{tr('Latest Activity')}</b><small>{tr('View Activity')} · {sorted.length} {tr('entries')}</small></span><ChevronDown className={open?'rotated':''} size={19}/></button>{open&&<><div className="activity-list transaction-history-list">{visible.map((x,i)=>{const negative=x.tone==='negative';return <div className="activity-item gpay-activity-item" key={`${x.sortKey||''}-${i}`}><span className={`transaction-icon ${negative?'debit':'credit'}`}>{negative?<ArrowUpFromLine size={17}/>:<ArrowDownToLine size={17}/>}</span><div className="activity-main"><b>{x.title}</b><span>{x.meta}</span></div><strong className={`activity-amount ${negative?'negative':x.tone==='neutral'?'neutral':'positive'}`}>{x.value}</strong></div>})}{!sorted.length&&<div className="empty-state">{tr('No data yet')}</div>}</div>{onLoadMore?<div ref={sentinelRef} className="infinite-feed-sentinel">{loadingMore?tr('Loading more…'):hasMore?tr('Scroll for more'):''}</div>:pages>1&&<Pagination page={page} total={sorted.length} pageSize={pageSize} onChange={setPage}/>}</>}</section>}
