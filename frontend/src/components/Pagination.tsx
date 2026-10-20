import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function Pagination({page,total,pageSize=5,onChange}:{page:number;total:number;pageSize?:number;onChange:(p:number)=>void}){const pages=Math.max(1,Math.ceil(total/pageSize));if(total<=pageSize)return null;return <div className="pagination"><button disabled={page<=1} onClick={()=>onChange(page-1)}>{tr('Previous')}</button>{Array.from({length:pages},(_,i)=>i+1).slice(Math.max(0,page-3),Math.min(pages,page+2)).map(p=><button key={p} className={p===page?'active':''} onClick={()=>onChange(p)}>{p}</button>)}<button disabled={page>=pages} onClick={()=>onChange(page+1)}>{tr('Next')}</button></div>}
