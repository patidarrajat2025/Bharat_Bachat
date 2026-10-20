import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function Logo({compact=false,src}:{compact?:boolean;src?:string|null}){
  const image=src||'/bharat-bachat-logo.png';
  return <div className={`brand ${compact?'brand-compact':''}`}>
    <img src={image} alt={tr("Bharat Bachat")} className={compact?'brand-mark brand-mark-lg':'brand-mark'} draggable={false} onError={e=>{e.currentTarget.src="/bharat-bachat-logo.png"}}/>
    {!compact&&<div className="brand-copy"><div className="brand-name">{tr("Bharat Bachat")}</div><div className="brand-tagline">{tr("Aapki Bachat, Aapka Vikas")}</div></div>}
  </div>;
}
