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
  const {i18n}=useTranslation(); const {tenant}=useStore(); const [theme,setTheme]=useState<'light'|'dark'>(()=>(localStorage.getItem('bb-settings-theme')==='dark'?'dark':'light'));
  useEffect(()=>{const apply=(next:'light'|'dark')=>{document.documentElement.dataset.theme=next;document.documentElement.style.colorScheme=next;localStorage.setItem('bb-settings-theme',next)};apply(theme);const listener=()=>setTheme(localStorage.getItem('bb-settings-theme')==='dark'?'dark':'light');window.addEventListener('bb-theme-change',listener);return()=>window.removeEventListener('bb-theme-change',listener)},[theme]);
  const chooseTheme=(next:'light'|'dark')=>{setTheme(next);document.documentElement.dataset.theme=next;document.documentElement.style.colorScheme=next;localStorage.setItem('bb-settings-theme',next)};
  const setLanguage=()=>{const n=i18n.language==='en'?'hi':'en';i18n.changeLanguage(n);localStorage.setItem('bb-settings-lang',n);document.documentElement.lang=n};
  return <div className="settings-panel">
    <div className="settings-row"><div><b>{tr('Language')}</b><span>{tr('Choose app language')}</span></div><button className="setting-pill" onClick={setLanguage}><span className="language-code">{i18n.language==='en'?'EN':'HI'}</span></button></div>
    <div className="settings-row settings-theme-row"><div><b>{tr('Appearance')}</b><span>{tr('Choose your preferred app theme')}</span></div><div className="appearance-toggle" role="group" aria-label={tr('Appearance')}>
      <button type="button" data-testid="theme-light-button" className={`appearance-option ${theme==='light'?'selected':''}`} aria-pressed={theme==='light'} onClick={()=>chooseTheme('light')}><Sun size={16}/><span>{tr('Light')}</span></button>
      <button type="button" data-testid="theme-dark-button" className={`appearance-option ${theme==='dark'?'selected':''}`} aria-pressed={theme==='dark'} onClick={()=>chooseTheme('dark')}><Moon size={16}/><span>{tr('Dark')}</span></button>
    </div></div>
    <p className="settings-help">{tr('Your choice is saved on this device and applies across the app.')}</p>
    {onDone&&<button type="button" className="btn-primary w-full mt-3" onClick={onDone}>{tr('Done')}</button>}
  </div>
}
