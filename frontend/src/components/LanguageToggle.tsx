import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function LanguageToggle(){
  const {i18n,t}=useTranslation();
  const next=i18n.language==='en'?'hi':'en';
  return <button type="button" className="language-toggle" aria-label={t('language')}
    onClick={()=>{i18n.changeLanguage(next);localStorage.setItem('bb-settings-lang',next);document.documentElement.lang=next}}>
    <span className="language-code">{i18n.language==='en'?'EN':'HI'}</span>
  </button>;
}
