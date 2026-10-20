import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function StatCard({label,value,icon}:{label:string;value:string;icon:ReactNode}){
  const key=String(label).toLowerCase();
  const tone=/(inflow|credit|contribution|collected|income)/.test(key)?'metric-inflow':/(outflow|debit|expense|repayment|penalty)/.test(key)?'metric-outflow':'';
  return <div className={`stat-card figma-stat-card ${tone}`}>
    <div className="stat-copy"><span>{label}</span><strong>{value}</strong></div>
    <div className="stat-icon">{icon}</div>
  </div>;
}
