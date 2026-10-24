import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function ThemeSettings({onDone}:{onDone?:()=>void}={}){
  const {i18n}=useTranslation(); const {tenant}=useStore();
  const setLanguage=()=>{const n=i18n.language==='en'?'hi':'en';i18n.changeLanguage(n);localStorage.setItem('bb-settings-lang',n);document.documentElement.lang=n};
  return <div className="settings-panel">
    <div className="settings-row"><div><b>{tr('Language')}</b><span>{tr('Choose app language')}</span></div><button className="setting-pill" onClick={setLanguage}><span className="language-code">{i18n.language==='en'?'EN':'HI'}</span></button></div>
    <div className="settings-row settings-theme-row"><div><b>{tr('Theme')}</b><span>{tenant?.name||tr('Group')} {tr('visual system')}</span></div><div className="theme-choice active figma-theme-lock"><span className="theme-swatch" style={{background:'linear-gradient(135deg,#047857,#065F46)'}}/><span>{tenant?.name||tr('Group')}</span><Check size={14}/></div></div>
    {onDone&&<button type="button" className="btn-primary w-full mt-3" onClick={onDone}>{tr('Done')}</button>}
  </div>
}
