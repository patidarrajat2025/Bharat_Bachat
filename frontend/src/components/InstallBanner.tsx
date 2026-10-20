import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';

type InstallPromptEvent = Event & { prompt:()=>Promise<void>; userChoice:Promise<{outcome:'accepted'|'dismissed'}> };


export function InstallButton(){
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
