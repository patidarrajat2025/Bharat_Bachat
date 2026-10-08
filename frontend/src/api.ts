import type { User, Tenant, Member, Loan, Transaction } from './types';

const envBase = String(import.meta.env.VITE_API_BASE_URL || '').trim();
const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
const protocol = typeof window !== 'undefined' ? window.location.protocol : 'http:';
const isPrivateHost = /^(localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})$/.test(host);
// Local LAN fallback makes the same build usable from a phone without hard-coding
// localhost. Production deployments should always provide VITE_API_BASE_URL.
const envIsLocalhost = /https?:\/\/(localhost|127\.0\.0\.1)(?::\d+)?/i.test(envBase);
const BASE = (isPrivateHost && (!envBase || envIsLocalhost))
  ? `${protocol}//${host}:8080/api`
  : (envBase || `${protocol}//${host}/api`);

const inFlight = new Map<string, Promise<unknown>>();
type CacheEntry={expiresAt:number;value:unknown};
const getCache = new Map<string,CacheEntry>();
const GET_TTL_MS = 5000;
const API_TIMING_LOG = String(import.meta.env.VITE_API_TIMING_LOG ?? 'true').toLowerCase() !== 'false';

function logApiTiming(message:string, data?:unknown){
  if(!API_TIMING_LOG || typeof console === 'undefined') return;
  if(data === undefined) console.info(message);
  else console.info(message, data);
}

export function invalidateApiCache(){ getCache.clear(); }

async function request<T>(path:string,options:RequestInit={}):Promise<T>{
  const token=localStorage.getItem('bb-token');
  const method=(options.method||'GET').toUpperCase();
  const headers=new Headers(options.headers);
  if(options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json');
  if(token) headers.set('Authorization',`Bearer ${token}`);

  const url=`${BASE}${path}`;
  const key=method==='GET' ? `${url}|${token||''}` : '';
  if(key){
    const cached=getCache.get(key);
    if(cached && cached.expiresAt>Date.now()) return cached.value as T;
    if(cached) getCache.delete(key);
    if(inFlight.has(key)) return inFlight.get(key) as Promise<T>;
  }

  const run=(async()=>{
    const started=typeof performance !== 'undefined' ? performance.now() : Date.now();
    const requestId=`web-${Math.random().toString(36).slice(2,8)}`;
    logApiTiming(`[api:start] ${requestId} ${method} ${path}`);
    try{
      const res=await fetch(url,{...options,headers});
      const serverMs=res.headers.get('X-Process-Time-Ms');
      const backendRequestId=res.headers.get('X-Request-Id');
      if(!res.ok){
        const b=await res.json().catch(()=>({}));
        if(res.status===401){ localStorage.removeItem('bb-token'); localStorage.removeItem('bb-user'); invalidateApiCache(); }
        throw new Error(b.detail||`Request failed: ${res.status}`);
      }
      const value=await res.json() as T;
      if(key) getCache.set(key,{expiresAt:Date.now()+GET_TTL_MS,value});
      else invalidateApiCache();
      const elapsed=(typeof performance !== 'undefined' ? performance.now() : Date.now())-started;
      logApiTiming(`[api:end] ${requestId} ${method} ${path}`,{
        status:res.status,
        totalMs:Math.round(elapsed),
        serverMs:serverMs ? Number(serverMs) : undefined,
        serverRequestId:backendRequestId || undefined,
      });
      return value;
    }catch(error:any){
      const elapsed=(typeof performance !== 'undefined' ? performance.now() : Date.now())-started;
      logApiTiming(`[api:end] ${requestId} ${method} ${path}`,{
        status:'error',
        totalMs:Math.round(elapsed),
        message:error?.message||String(error),
      });
      if(error instanceof TypeError) throw new Error('Network request failed');
      throw error;
    }
  })();

  if(key){
    inFlight.set(key,run);
    run.finally(()=>inFlight.delete(key)).catch(()=>{});
  }
  return run;
}


export function prefetchGroupRoute(tid:string, route:string, role?:string, memberId?:string){
  if(!tid) return;
  const safe = (promise:Promise<unknown>) => { void promise.catch(()=>null); };
  if(route==='/members') return safe(api.members(tid));
  if(route==='/register') return safe(api.registerOverview(tid));
  if(route==='/ledger') return safe(api.ledgerOverview(tid));
  if(route==='/analytics'){
    if(role==='member' && memberId) return safe(Promise.all([api.passbook(tid,memberId),api.analytics(tid,12,undefined,memberId)]).then(()=>undefined));
    return safe(Promise.all([api.members(tid),api.analytics(tid,12)]).then(()=>undefined));
  }
  if(route==='/passbook'){
    if(role==='member' && memberId) return safe(api.passbook(tid,memberId));
    return safe(api.members(tid).then(ms=>{const first=ms[0]?._id; return first?api.passbook(tid,first):undefined;}));
  }
  if(route==='/loans') return safe(api.loansOverview(tid));
  if(route==='/personal-loan') return safe(api.personalLoanOverview(tid));
  if(route==='/admin' && role!=='super_admin') return safe(api.adminOverview(tid));
}

export const api={
 login:(phone:string,password:string)=>request<{access_token:string;user:User}>('/auth/login',{method:'POST',body:JSON.stringify({phone,password})}),
 me:()=>request<User>('/auth/me'),
 uploadProfileImage:(file:File)=>{const fd=new FormData();fd.append('file',file);return request<any>('/auth/profile-image',{method:'POST',body:fd})},
 deleteProfileImage:()=>request<any>('/auth/profile-image',{method:'DELETE'}),
 changePassword:(body:any)=>request<any>('/auth/change-password',{method:'POST',body:JSON.stringify(body)}),
 tenants:()=>request<Tenant[]>('/super-admin/tenants'),
 createTenant:(body:any)=>request<any>('/super-admin/tenants',{method:'POST',body:JSON.stringify(body)}),
 uploadTenantLogo:(id:string,file:File)=>{const fd=new FormData();fd.append('file',file);return request<any>(`/super-admin/tenants/${id}/logo`,{method:'POST',body:fd})},
 uploadGroupLogo:(id:string,file:File)=>{const fd=new FormData();fd.append('file',file);return request<any>(`/group/${id}/logo`,{method:'POST',body:fd})},
 tenantStatus:(id:string,active:boolean)=>request<any>(`/super-admin/tenants/${id}/status`,{method:'PATCH',body:JSON.stringify({active})}),
 tenantSettings:(id:string,kist_per_share:number)=>request<any>(`/super-admin/tenants/${id}/settings`,{method:'PATCH',body:JSON.stringify({kist_per_share})}),
 admins:()=>request<any[]>('/super-admin/admins'),
 createAdmin:(body:any)=>request<any>('/super-admin/admins',{method:'POST',body:JSON.stringify(body)}),
 adminStatus:(id:string,active:boolean)=>request<any>(`/super-admin/users/${id}/status`,{method:'PATCH',body:JSON.stringify({active})}),
 resetUserPassword:(id:string,password:string)=>request<any>(`/super-admin/users/${id}/reset-password`,{method:'POST',body:JSON.stringify({password})}),
 dashboard:(id:string)=>request<any>(`/group/${id}/dashboard`),
 accounting:(id:string,view:string,params="")=>request<any>(`/group/${id}/accounting/${view}${params}`),
 adminOverview:(id:string)=>request<any>(`/group/${id}/admin-overview`),
 summary:(id:string,memberId?:string)=>request<any>(`/group/${id}/summary${memberId?`?member_id=${encodeURIComponent(memberId)}`:''}`),
 tenant:(id:string)=>request<Tenant>(`/group/${id}`),
 analytics:(id:string,months?:number,shareNo?:number,memberId?:string)=>{const q=new URLSearchParams();if(months!==undefined)q.set('months',String(months));if(shareNo!==undefined)q.set('share_no',String(shareNo));if(memberId)q.set('member_id',memberId);const qs=q.toString();return request<any[]>(`/group/${id}/analytics${qs?`?${qs}`:''}`)},
 members:(id:string)=>request<Member[]>(`/group/${id}/members`),
 memberShares:(tid:string,mid:string)=>request<import('./types').Share[]>(`/group/${tid}/members/${mid}/shares`),
 memberDetails:(tid:string,mid:string)=>request<any>(`/group/${tid}/members/${mid}/details`),
 addMember:(id:string,b:any)=>request<any>(`/group/${id}/members`,{method:'POST',body:JSON.stringify(b)}),
 updateMember:(tid:string,mid:string,b:any)=>request<any>(`/group/${tid}/members/${mid}`,{method:'PATCH',body:JSON.stringify(b)}),
 memberStatus:(tid:string,mid:string,active:boolean)=>request<any>(`/group/${tid}/members/${mid}/status`,{method:'PATCH',body:JSON.stringify({active})}),
 resetMemberPassword:(tid:string,mid:string,password:string)=>request<any>(`/group/${tid}/members/${mid}/reset-password`,{method:'POST',body:JSON.stringify({password})}),
 uploadMemberProfileImage:(tid:string,mid:string,file:File)=>{const fd=new FormData();fd.append('file',file);return request<any>(`/group/${tid}/members/${mid}/profile-image`,{method:'POST',body:fd})},
 deleteMemberProfileImage:(tid:string,mid:string)=>request<any>(`/group/${tid}/members/${mid}/profile-image`,{method:'DELETE'}),
 registerOverview:(tid:string)=>request<any>(`/group/${tid}/register-overview`),
 ledgerOverview:(tid:string)=>request<any>(`/group/${tid}/ledger-overview`),
 loansOverview:(tid:string)=>request<any>(`/group/${tid}/loans-overview`),
  groupSettings:(tid:string)=>request<any>(`/group/${tid}/settings`),
  updateGroupSettings:(tid:string,b:any)=>request<any>(`/group/${tid}/settings`,{method:'PATCH',body:JSON.stringify(b)}),
  loanEligibility:(tid:string,memberId:string,amount?:number,months?:number)=>{const q=new URLSearchParams();if(amount!==undefined&&amount>0)q.set('amount',String(amount));if(months!==undefined)q.set('months',String(months));const qs=q.toString();return request<any>(`/group/${tid}/loan-eligibility/${memberId}${qs?`?${qs}`:''}`)},
 personalLoanOverview:(tid:string)=>request<any>(`/group/${tid}/personal-loan-overview`),
 transactions:(tid:string,params='')=>request<Transaction[]>(`/group/${tid}/transactions${params}`),
 groupActivity:(tid:string,page=1,pageSize=10)=>request<any[]>(`/group/${tid}/activity?page=${page}&page_size=${pageSize}`),
 contributions:(tid:string,b:any)=>request<any>(`/group/${tid}/contributions`,{method:'POST',body:JSON.stringify(b)}),
 monthlyKistSummary:(tid:string,period:string)=>request<any>(`/group/${tid}/monthly-kist-summary?period=${encodeURIComponent(period)}`),
 monthlyKistStatus:(tid:string,mid:string,period:string)=>request<any>(`/group/${tid}/monthly-kist/${mid}?period=${encodeURIComponent(period)}`),
 monthlyKist:(tid:string,b:any)=>request<any>(`/group/${tid}/monthly-kist`,{method:'POST',body:JSON.stringify(b)}),
 monthlyKistBulkStatus:(tid:string,period:string)=>request<any>(`/group/${tid}/monthly-kist-bulk-status?period=${encodeURIComponent(period)}`),
 monthlyKistBulk:(tid:string,b:any)=>request<any>(`/group/${tid}/monthly-kist-bulk`,{method:'POST',body:JSON.stringify(b)}),
 moneyIn:(tid:string,b:any)=>request<any>(`/group/${tid}/money-in`,{method:'POST',body:JSON.stringify(b)}),
 loans:(tid:string,memberId?:string)=>request<Loan[]>(`/group/${tid}/loans${memberId?`?member_id=${encodeURIComponent(memberId)}`:''}`),
 groupLoans:(tid:string)=>request<any[]>(`/group/${tid}/group-loans`),
 createLoan:(tid:string,b:any)=>request<any>(`/group/${tid}/loans`,{method:'POST',body:JSON.stringify(b)}),
 loanPayment:(tid:string,b:any)=>request<any>(`/group/${tid}/loan-payments`,{method:'POST',body:JSON.stringify(b)}),
 loanRequests:(tid:string)=>request<any[]>(`/group/${tid}/loan-requests`),
 createLoanRequest:(tid:string,b:any)=>request<any>(`/group/${tid}/loan-requests`,{method:'POST',body:JSON.stringify(b)}),
 decideLoanRequest:(tid:string,rid:string,b:any)=>request<any>(`/group/${tid}/loan-requests/${rid}`,{method:'PATCH',body:JSON.stringify(b)}),
 expenses:(tid:string)=>request<any[]>(`/group/${tid}/expenses`),
 categories:(tid:string)=>request<any[]>(`/group/${tid}/expense-categories`),
 createCategory:(tid:string,b:any)=>request<any>(`/group/${tid}/expense-categories`,{method:'POST',body:JSON.stringify(b)}),
 createExpense:(tid:string,b:any)=>request<any>(`/group/${tid}/expenses`,{method:'POST',body:JSON.stringify(b)}),
 audit:(tid:string)=>request<any[]>(`/group/${tid}/audit`),
 uploadExpenseProof:(tid:string,id:string,file:File)=>{const fd=new FormData();fd.append('file',file);return request<any>(`/group/${tid}/expenses/${id}/proof`,{method:'POST',body:fd})},
 passbook:(tid:string,mid:string,params='')=>request<Transaction[]>(`/group/${tid}/passbook/${mid}${params}`),
 receiptUrl:(tid:string,id:string)=>`${BASE}/reports/receipt/${tid}/${id}`,
 passbookPdfUrl:(tid:string,id:string,params='')=>`${BASE}/reports/passbook/${tid}/${id}${params}`,
 receiptBundleUrl:(tid:string,id:string,params='')=>`${BASE}/reports/receipt-bundle/${tid}/${id}${params}`,
 monthlyKistReceiptUrl:(tid:string,id:string,period:string)=>`${BASE}/reports/monthly-kist/${tid}/${id}/${encodeURIComponent(period)}`,
 notifications:(tid:string)=>request<any[]>(`/group/${tid}/notifications`),
 markNotificationRead:(tid:string,id:string)=>request<any>(`/group/${tid}/notifications/${id}/read`,{method:'PATCH'}),
  clearNotifications:(tid:string,ids:string[])=>request<any>(`/group/${tid}/notifications/clear`,{method:'POST',body:JSON.stringify({ids})}),
 deleteNotification:(tid:string,id:string)=>request<any>(`/group/${tid}/notifications/${id}`,{method:'DELETE'}),
 fetchBlob:async(url:string)=>{
    const started=typeof performance !== 'undefined' ? performance.now() : Date.now();
    const requestId=`web-${Math.random().toString(36).slice(2,8)}`;
    logApiTiming(`[api:start] ${requestId} GET ${url}`);
    try{
      const token=localStorage.getItem('bb-token');
      const r=await fetch(url,{headers:{Authorization:`Bearer ${token}`}});
      if(!r.ok) throw new Error('Unable to generate PDF');
      const blob=await r.blob();
      const elapsed=(typeof performance !== 'undefined' ? performance.now() : Date.now())-started;
      logApiTiming(`[api:end] ${requestId} GET ${new URL(url,window.location.origin).pathname}`,{
        status:r.status,
        totalMs:Math.round(elapsed),
        serverMs:r.headers.get('X-Process-Time-Ms') ? Number(r.headers.get('X-Process-Time-Ms')) : undefined,
        serverRequestId:r.headers.get('X-Request-Id') || undefined,
      });
      return blob;
    }catch(error:any){
      const elapsed=(typeof performance !== 'undefined' ? performance.now() : Date.now())-started;
      logApiTiming(`[api:end] ${requestId} GET ${url}`,{status:'error',totalMs:Math.round(elapsed),message:error?.message||String(error)});
      throw error;
    }
  },
};
