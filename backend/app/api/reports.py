from datetime import date, datetime, time, timezone, timedelta
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from ..deps import current_user, tenant_guard, parse_oid
from ..db import get_db
from ..pdf import receipt_pdf, passbook_pdf, receipt_bundle_pdf, monthly_kist_statement_pdf, get_cached_logo_bytes
from ..accounting_engine import to_minor, from_minor
router=APIRouter(prefix="/reports",tags=["reports"])
@router.get("/receipt/{tenant_id}/{transaction_id}")
async def receipt(tenant_id:str,transaction_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db(); tx=await db.transactions.find_one({"_id":parse_oid(transaction_id),"tenant_id":tenant_id})
    if not tx: raise HTTPException(404,"Transaction not found")
    if user["role"]=="member" and str(user.get("member_id"))!=tx.get("member_id"): raise HTTPException(403,"Access denied")
    m,t=await asyncio.gather(db.members.find_one({"_id":parse_oid(tx["member_id"]),"tenant_id":tenant_id}),db.tenants.find_one({"_id":parse_oid(tenant_id)}))
    if not m or not t: raise HTTPException(404,"Member or group not found")
    logo_bytes=(await get_cached_logo_bytes(t.get("logo_url"))) or b''; pdf=receipt_pdf(t["name"],f'{m.get("first_name","")} {m.get("last_name","")}'.strip(),tx.get("amount",0),f'{tx.get("type","Transaction")} · Share {tx.get("share_no")}' if tx.get("share_no") else tx.get("type","Transaction"),transaction_id,str(tx.get("date",""))[:10],tx.get("account",""),t.get("logo_url"),tx.get("note",""),logo_bytes)
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f'inline; filename="receipt-{transaction_id}.pdf"'})
@router.get("/passbook/{tenant_id}/{member_id}")
async def passbook(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Access denied")
    db=get_db(); m,t=await asyncio.gather(db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id}),db.tenants.find_one({"_id":parse_oid(tenant_id)}))
    if not m or not t: raise HTTPException(404,"Member or group not found")
    q={"tenant_id":tenant_id,"member_id":member_id}
    if share_no:q["share_no"]=share_no
    # Full history is used to calculate a stable brought-forward balance before
    # applying date filters; the exported rows are always chronological.
    all_rows=await db.transactions.find(q,{"_id":1,"date":1,"created_at":1,"amount":1,"amount_minor":1,"type":1,"original_type":1,"account":1,"share_no":1,"share_id":1,"note":1,"transaction_ref":1}).to_list(100000)
    all_rows.sort(key=lambda r:(str(r.get("date") or r.get("created_at") or "")[:10], 0 if float(r.get("amount",0) or 0)>=0 else 1, str(r.get("created_at") or r.get("date") or ""), str(r.get("_id") or "")))
    opening=to_minor(m.get("opening_balance",m.get("opening_deposit",0)) or 0)
    statement_opening_minor=opening; balance_minor=opening; out=[]
    for r in all_rows:
        amount_minor=int(r["amount_minor"]) if r.get("amount_minor") is not None else to_minor(r.get("amount",0) or 0)
        balance_minor += amount_minor
        d=r.get("date")
        if from_date and d and d.date()<from_date:
            statement_opening_minor=balance_minor
            continue
        if to_date and d and d.date()>to_date: continue
        x=dict(r); x["date"]=str(x.get("date", ""))[:10]; x["amount"]=float(from_minor(amount_minor)); x["running_balance"]=float(from_minor(balance_minor)); out.append(x)
    logo_bytes=(await get_cached_logo_bytes(t.get("logo_url"))) or b''
    pdf=passbook_pdf(t["name"],f'{m.get("first_name","")} {m.get("last_name","")}'.strip(),out,t.get("logo_url"),logo_bytes,float(from_minor(statement_opening_minor)))
    return StreamingResponse(pdf,media_type="application/pdf",headers={'Content-Disposition':'attachment; filename="passbook.pdf"'})

@router.get("/receipt-bundle/{tenant_id}/{member_id}")
async def receipt_bundle(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Access denied")
    db=get_db(); m,t=await asyncio.gather(db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id}),db.tenants.find_one({"_id":parse_oid(tenant_id)}))
    if not m or not t: raise HTTPException(404,"Member or group not found")
    q={"tenant_id":tenant_id,"member_id":member_id}
    if share_no:q["share_no"]=share_no
    all_rows=await db.transactions.find(q,{"_id":1,"date":1,"created_at":1,"amount":1,"amount_minor":1,"type":1,"original_type":1,"account":1,"share_no":1,"share_id":1,"note":1,"transaction_ref":1}).to_list(100000)
    all_rows.sort(key=lambda r:(str(r.get("date") or r.get("created_at") or "")[:10], 0 if float(r.get("amount",0) or 0)>=0 else 1, str(r.get("created_at") or r.get("date") or ""), str(r.get("_id") or "")))
    opening_minor=to_minor(m.get("opening_balance",m.get("opening_deposit",0)) or 0); statement_opening_minor=opening_minor; bal_minor=opening_minor; out=[]
    for r in all_rows:
        amount_minor=int(r["amount_minor"]) if r.get("amount_minor") is not None else to_minor(r.get("amount",0) or 0); bal_minor += amount_minor; d=r.get("date")
        if from_date and d and d.date()<from_date:
            statement_opening_minor=bal_minor
            continue
        if to_date and d and d.date()>to_date: continue
        x=dict(r); x["date"]=str(x.get("date",""))[:10]; x["amount"]=float(from_minor(amount_minor)); x["running_balance"]=float(from_minor(bal_minor)); out.append(x)
    logo_bytes=(await get_cached_logo_bytes(t.get("logo_url"))) or b''; pdf=receipt_bundle_pdf(t["name"],f'{m.get("first_name","")} {m.get("last_name","")}'.strip(),out,t.get("logo_url"),logo_bytes,float(from_minor(statement_opening_minor)))
    safe_name=f"BC_{str(t.get('code') or 'Group')}_Receipt_{str(member_id)[:6]}.pdf"
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{safe_name}"'})

@router.get("/monthly-kist/{tenant_id}/{member_id}/{period}")
async def monthly_kist_receipt(tenant_id:str,member_id:str,period:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user.get("role") not in {"group_admin", "super_admin"}:
        raise HTTPException(403,"Monthly Kist receipts are available to administrators only")
    try:
        y,m=map(int,period.split("-")); start=date(y,m,1); end=date(y+1,1,1) if m==12 else date(y,m+1,1)
    except Exception: raise HTTPException(400,"Invalid period")
    db=get_db(); member,tenant=await asyncio.gather(db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id}),db.tenants.find_one({"_id":parse_oid(tenant_id)}))
    if not member or not tenant: raise HTTPException(404,"Member or group not found")
    q={"tenant_id":tenant_id,"member_id":member_id,"date":{"$gte":datetime.combine(start,time.min,tzinfo=timezone.utc),"$lt":datetime.combine(end,time.min,tzinfo=timezone.utc)}}
    rows=await db.transactions.find(q,{"_id":1,"date":1,"amount":1,"amount_minor":1,"type":1,"original_type":1,"account":1,"share_no":1,"share_id":1,"note":1,"transaction_ref":1,"payment_category":1,"penalty_category":1,"principal":1,"principal_paid":1,"loan_interest_collected":1,"interest":1,"loan_penalty_collected":1,"bc_regular_kist_penalty":1,"other_interest":1,"other_penalty":1,"period":1,"created_at":1}).sort([("date",1),("created_at",1),("_id",1)]).to_list(5000)
    out=[]
    for r in rows:
        x=dict(r); typ=str(x.get("type", "transaction")); category=str(x.get("penalty_category") or "").lower()
        is_kist=x.get("payment_category")=="monthly_kist"
        is_loan=typ in {"loan_repayment"} or (typ=="reversal" and x.get("original_type")=="loan_repayment")
        is_penalty=("penalt" in typ.lower()) or bool(category) or float(x.get("loan_penalty_collected",0) or 0)>0 or float(x.get("bc_regular_kist_penalty",0) or 0)>0 or float(x.get("other_penalty",0) or 0)>0
        if not (is_kist or is_loan or is_penalty): continue
        x["date"]=str(x.get("date", ""))[:10]; x["amount"]=float(from_minor(int(x["amount_minor"]))) if x.get("amount_minor") is not None else float(x.get("amount",0) or 0)
        x["statement_label"] = ("Monthly BC Kist" if is_kist else "Personal Penalty" if is_penalty and not is_loan else "Loan Installment")
        if is_loan: x["principal_paid"]=float(x.get("principal_paid", x.get("principal",0)) or 0)
        out.append(x)
    logo_bytes=(await get_cached_logo_bytes(tenant.get("logo_url"))) or b''; member_name=f'{member.get("first_name","")} {member.get("last_name","")}'.strip(); active_shares=await db.shares.count_documents({"tenant_id":tenant_id,"member_id":member_id,"status":"active"}); active_shares=active_shares or int(member.get("active_shares_count",member.get("shares",0)) or 0); pdf=monthly_kist_statement_pdf(tenant["name"],member_name,period,out,tenant.get("logo_url"),logo_bytes,member_id,active_shares,tenant.get("code", ""))
    safe_name=f"BC_{str(tenant.get('code') or 'Group')}_Receipt_{period}_{str(member_id)[:6]}.pdf"
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{safe_name}"'})
