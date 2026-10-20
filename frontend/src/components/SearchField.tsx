import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, BarChart3, BookOpen, ChevronDown, CreditCard, FileText, Home, LogOut, MoreHorizontal, Receipt, ShieldCheck, Smartphone, UserCircle, Users, WalletCards, X, Bell, Settings, Sun, Moon, Palette, Check, ArrowDownToLine, ArrowUpFromLine, Info } from 'lucide-react';
import { useStore } from '../store';
import { tr, trError, trDynamic, appLocale } from '../i18n';
import { api } from '../api';


export function SearchField({value,onChange,placeholder,ariaLabel}:{value:string;onChange:(value:string)=>void;placeholder:string;ariaLabel?:string}){return <div className="search-field-wrap"><input className="data-search" value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} aria-label={ariaLabel||placeholder}/>{value&&<button type="button" className="search-clear-btn" aria-label={tr('Clear')} onClick={()=>onChange('')}><X size={17}/></button>}</div>}
