from datetime import date, datetime, time, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from ..deps import current_user, tenant_guard, parse_oid
from ..db import get_db
from ..pdf import receipt_pdf, passbook_pdf, receipt_bundle_pdf
router=APIRouter(prefix="/reports",tags=["reports"])
@router.get("/receipt/{tenant_id}/{transaction_id}")
async def receipt(tenant_id:str,transaction_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db(); tx=await db.transactions.find_one({"_id":parse_oid(transaction_id),"tenant_id":tenant_id})
    if not tx: raise HTTPException(404,"Transaction not found")
    if user["role"]=="member" and str(user.get("member_id"))!=tx.get("member_id"): raise HTTPException(403,"Access denied")
    m=await db.members.find_one({"_id":parse_oid(tx["member_id"]),"tenant_id":tenant_id}); t=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    pdf=receipt_pdf(t["name"],f'{m.get("first_name","")} {m.get("last_name","")}'.strip(),tx.get("amount",0),f'{tx.get("type","Transaction")} · Share {tx.get("share_no")}' if tx.get("share_no") else tx.get("type","Transaction"),transaction_id,str(tx.get("date",""))[:10],tx.get("account",""),t.get("logo_url"),tx.get("note",""))
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f'inline; filename="receipt-{transaction_id}.pdf"'})
@router.get("/passbook/{tenant_id}/{member_id}")
async def passbook(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Access denied")
    db=get_db(); m=await db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id}); t=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    q={"tenant_id":tenant_id,"member_id":member_id}
    if share_no:q["share_no"]=share_no
    all_rows=await db.transactions.find(q).sort("date",1).to_list(10000)
    bal=0; out=[]
    for r in all_rows:
        bal += float(r.get("amount",0))
        d=r.get("date")
        if from_date and d and d.date()<from_date: continue
        if to_date and d and d.date()>to_date: continue
        x=dict(r); x["date"]=str(x.get("date",""))[:10]; x["amount"]=float(x.get("amount",0)); x["running_balance"]=round(bal,2); out.append(x)
    pdf=passbook_pdf(t["name"],f'{m.get("first_name","")} {m.get("last_name","")}'.strip(),out,t.get("logo_url"))
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":'attachment; filename="passbook.pdf"'})

@router.get("/receipt-bundle/{tenant_id}/{member_id}")
async def receipt_bundle(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Access denied")
    db=get_db(); m=await db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id}); t=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not m or not t: raise HTTPException(404,"Member or group not found")
    q={"tenant_id":tenant_id,"member_id":member_id}
    if share_no:q["share_no"]=share_no
    all_rows=await db.transactions.find(q).sort("date",1).to_list(10000)
    bal=0; out=[]
    for r in all_rows:
        bal += float(r.get("amount",0) or 0); d=r.get("date")
        if from_date and d and d.date()<from_date: continue
        if to_date and d and d.date()>to_date: continue
        x=dict(r); x["date"]=str(x.get("date",""))[:10]; x["amount"]=float(x.get("amount",0) or 0); x["running_balance"]=round(bal,2); out.append(x)
    pdf=receipt_bundle_pdf(t["name"],f'{m.get("first_name","")} {m.get("last_name","")}'.strip(),out,t.get("logo_url"))
    safe_name=f"BC_{str(t.get('code') or 'Group')}_Receipt_{str(member_id)[:6]}.pdf"
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{safe_name}"'})

@router.get("/monthly-kist/{tenant_id}/{member_id}/{period}")
async def monthly_kist_receipt(tenant_id:str,member_id:str,period:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Access denied")
    try:
        y,m=map(int,period.split("-")); start=date(y,m,1); end=date(y+1,1,1) if m==12 else date(y,m+1,1)
    except Exception: raise HTTPException(400,"Invalid period")
    db=get_db(); member=await db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id}); tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not member or not tenant: raise HTTPException(404,"Member or group not found")
    q={"tenant_id":tenant_id,"member_id":member_id,"date":{"$gte":datetime.combine(start,time.min,tzinfo=timezone.utc),"$lt":datetime.combine(end,time.min,tzinfo=timezone.utc)}}
    rows=await db.transactions.find(q).sort("date",1).to_list(5000); bal=0; out=[]
    for r in rows:
        bal += float(r.get("amount",0) or 0); x=dict(r); x["date"]=str(x.get("date",""))[:10]; x["amount"]=float(x.get("amount",0) or 0); x["running_balance"]=round(bal,2); out.append(x)
    pdf=receipt_bundle_pdf(tenant["name"],f'{member.get("first_name","")} {member.get("last_name","")}'.strip(),out,tenant.get("logo_url"))
    safe_name=f"BC_{str(tenant.get('code') or 'Group')}_Receipt_{period}_{str(member_id)[:6]}.pdf"
    return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{safe_name}"'})
