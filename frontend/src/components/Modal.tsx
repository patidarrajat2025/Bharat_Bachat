import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}){
  const content=<div className="modal-backdrop" role="dialog" aria-modal="true" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div className={`modal-card modal-slide-up ${/Loan|loan|Request|request/.test(title)?'loan-modal':''}`}>
      <div className="modal-head flex items-center justify-between gap-3 px-4 py-3"><div className="modal-title-inline flex min-w-0 items-center gap-2"><button type="button" className="modal-back-button" onClick={onClose} aria-label={tr("Back")}><ArrowLeft size={17}/><span>{tr("Back")}</span></button>{/Monthly Kist Collection|Bulk Monthly Kist/.test(title)&&<Info size={18} aria-hidden="true"/>}<h2>{title}</h2></div><button type="button" className="icon-btn shrink-0 ml-auto" aria-label={tr("Close")} onClick={onClose}><X size={19}/></button></div>
      <div className="modal-body">{children}</div>
    </div>
  </div>;
  return typeof document==='undefined'?content:createPortal(content,document.body);
}
