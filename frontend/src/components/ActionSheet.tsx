import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';

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
