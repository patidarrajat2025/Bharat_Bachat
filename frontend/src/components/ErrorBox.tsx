import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function ErrorBox({error,onClose}:{error:string;onClose?:()=>void}){
  const [dismissed,setDismissed]=useState(false);
  useEffect(()=>setDismissed(false),[error]);
  useEffect(()=>{if(!error)return;const timer=window.setTimeout(()=>{setDismissed(true);onClose?.()},1000);return()=>window.clearTimeout(timer)},[error,onClose]);
  if(!error||dismissed)return null;
  return <div className="error-banner" role="alert"><div className="error-content"><span className="error-dot"/><span>{trError(error)}</span></div></div>;
}
