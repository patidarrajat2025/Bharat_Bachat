from __future__ import annotations
from datetime import datetime, timezone, date
from typing import Literal
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from bson import ObjectId
from pymongo import ReturnDocument
from app.db import get_db
from app.deps import current_user, tenant_guard, parse_oid
from app.accounting_engine import to_minor, from_minor
from app.transactions import run_in_transaction
from app.journal import build_journal, reverse_journal
from app.core.config import settings

router = APIRouter(tags=["investments"])

class InvestmentCreate(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    description: str = ""
    purchase_date: date | None = None
    purchase_amount: float = Field(gt=0)
    quantity: float | None = Field(default=None, gt=0)
    vendor: str = ""
    reference_number: str = ""
    account: Literal["cash", "bank"] = "cash"
    payment_amount: float = Field(gt=0)
    current_estimated_value: float | None = Field(default=None, ge=0)
    maturity_date: date | None = None
    expected_return: float | None = Field(default=None, ge=0)
    notes: str = ""

class InvestmentReturn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    capital_return_minor: int = Field(ge=0)
    proceeds_minor: int = Field(gt=0)
    account: Literal["cash", "bank"] = "cash"
    transaction_date: date | None = None
    notes: str = ""
    close: bool = False

class InvestmentInterest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    amount: float = Field(gt=0)
    account: Literal["cash", "bank"] = "bank"
    transaction_date: date | None = None
    notes: str = ""

class ValuationUpdate(BaseModel):
    estimated_value: float = Field(ge=0)
    valuation_date: date | None = None
    notes: str = ""

class InvestmentUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    description: str = ""
    vendor: str = ""
    reference_number: str = ""
    maturity_date: date | None = None
    expected_return: float | None = Field(default=None, ge=0)
    notes: str = ""

class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)


def _date(value):
    return datetime.combine(value or date.today(), datetime.min.time(), tzinfo=timezone.utc)

def _serialize(row):
    if not row: return None
    out = dict(row); out["_id"] = str(out["_id"])
    for key in ("created_by", "investment_id", "transaction_id", "reversal_of", "reversed_by"):
        if isinstance(out.get(key), ObjectId): out[key] = str(out[key])
    return out

async def _account_balance(session, tenant_id: str, account: str, tenant: dict) -> int:
    db = get_db()
    tx = await db.transactions.aggregate([
        {"$match": {"tenant_id": tenant_id, "account": account}},
        {"$group": {"_id": None, "minor": {"$sum": {"$ifNull": ["$amount_minor", {"$round": [{"$multiply": [{"$ifNull": ["$amount", 0]}, 100]}, 0]}]}}}}
    ], session=session).to_list(1)
    expenses = await db.expenses.aggregate([
        {"$match": {"tenant_id": tenant_id, "account": account}},
        {"$group": {"_id": None, "minor": {"$sum": {"$ifNull": ["$amount_minor", {"$round": [{"$multiply": [{"$ifNull": ["$amount", 0]}, 100]}, 0]}]}}}}
    ], session=session).to_list(1)
    opening = to_minor(tenant.get("opening_cash" if account == "cash" else "opening_bank", 0) or 0)
    return opening + int((tx[0] if tx else {}).get("minor", 0) or 0) - int((expenses[0] if expenses else {}).get("minor", 0) or 0)

async def _post_money(session, *, tenant_id, actor, amount_minor, account, typ, investment_id, note, date_value, idempotency_key, capital_minor=0, profit_minor=0, loss_minor=0):
    db=get_db(); now=datetime.now(timezone.utc); txid=ObjectId()
    doc={"_id":txid,"tenant_id":tenant_id,"member_id":None,"type":typ,"amount":float(from_minor(amount_minor)),"amount_minor":int(amount_minor),"account":account,"transaction_ref":f"BB-INV-{now.strftime('%Y%m%d')}-{txid.hex[:8].upper()}","date":date_value,"created_at":now,"idempotency_key":idempotency_key,"created_by":str(actor.get("_id","system")),"created_by_role":actor.get("role"),"note":note,"investment_id":str(investment_id),"investment_capital_minor":int(capital_minor),"investment_realized_profit_minor":int(profit_minor),"investment_realized_loss_minor":int(loss_minor),"side_effects_status":"complete","journal_status":"pending"}
    journal=build_journal(doc)
    if not journal: raise HTTPException(500,"Unable to construct balanced investment journal")
    await db.transactions.insert_one(doc, session=session)
    journal_doc={"tenant_id":tenant_id,"source_type":"transaction","source_id":str(txid),"source_key":f"tx:{txid}",**journal,"status":"posted","created_at":now,"posted_at":now}
    await db.journal_entries.insert_one(journal_doc, session=session)
    await db.transactions.update_one({"_id":txid,"tenant_id":tenant_id},{"$set":{"journal_status":"posted"}},session=session)
    feed={"source_key":f"tx:{txid}","source_type":"transaction","source_id":str(txid),"transaction_ref":doc["transaction_ref"],"tenant_id":tenant_id,"member_id":None,"type":typ,"amount":doc["amount"],"amount_minor":amount_minor,"account":account,"date":date_value,"created_at":now,"note":note,"investment_id":str(investment_id),"investment_capital_minor":int(capital_minor),"investment_realized_profit_minor":int(profit_minor),"investment_realized_loss_minor":int(loss_minor)}
    await db.financial_feed.insert_one(feed,session=session)
    await db.audit_logs.insert_one({"tenant_id":tenant_id,"actor_id":str(actor.get("_id","system")),"actor_phone":actor.get("phone"),"action":typ.upper()+"_POSTED","entity":"investment","entity_id":str(investment_id),"details":{"transaction_id":str(txid),"amount_minor":amount_minor},"event_key":f"investment-tx:{idempotency_key}","created_at":now},session=session)
    return txid

@router.get("/group/{tenant_id}/investments")
async def list_investments(tenant_id: str, user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    rows=await db.group_investments.find({"tenant_id":tenant_id}).sort("created_at",-1).to_list(2000)
    txs=await db.investment_transactions.find({"tenant_id":tenant_id}).sort("date",-1).to_list(5000)
    vals=await db.investment_valuations.find({"tenant_id":tenant_id}).sort("date",-1).to_list(5000)
    docs=await db.investment_documents.find({"tenant_id":tenant_id},{"path":0}).sort("created_at",-1).to_list(2000)
    names={str(x["_id"]):x.get("name", "Investment") for x in rows}
    custom=await db.investment_categories.find({"tenant_id":tenant_id}).sort("name",1).to_list(500)
    for row in txs: row["investment_name"]=names.get(str(row.get("investment_id")), "Investment")
    for row in vals: row["investment_name"]=names.get(str(row.get("investment_id")), "Investment")
    active=[x for x in rows if x.get("status")=="active"]
    closed=[x for x in rows if x.get("status")=="closed"]
    reportable=[x for x in rows if x.get("status")!="reversed"]
    overview={"total_invested_minor":sum(int(x.get("original_amount_minor",0)) for x in reportable),"capital_returned_minor":sum(int(x.get("capital_returned_minor",0)) for x in reportable),"remaining_capital_minor":sum(int(x.get("remaining_capital_minor",0)) for x in reportable),"estimated_value_minor":sum(int(x.get("current_value_minor",x.get("remaining_capital_minor",0))) for x in active),"realized_profit_minor":sum(int(x.get("realized_profit_minor",0)) for x in reportable),"realized_loss_minor":sum(int(x.get("realized_loss_minor",0)) for x in reportable),"interest_received_minor":sum(int(x.get("interest_received_minor",0)) for x in reportable),"active_count":len(active),"closed_count":len(closed)}
    return {"overview":overview,"investments":[_serialize(x) for x in rows],"transactions":[_serialize(x) for x in txs],"valuations":[_serialize(x) for x in vals],"documents":[_serialize(x) for x in docs],"categories":["Gold","Fixed Deposit (FD)","Recurring Deposit (RD)","Plot / Land","Property","Physical Assets","Other"]+[x.get("name") for x in custom]}

@router.post("/group/{tenant_id}/investments")
async def create_investment(tenant_id: str, body: InvestmentCreate, user=Depends(current_user)):
    if user.get("role") not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    await tenant_guard(user,tenant_id); db=get_db(); amount=to_minor(body.purchase_amount); paid=to_minor(body.payment_amount)
    if paid != amount: raise HTTPException(400,"Partial payment is not enabled until payable/liability posting is supported; enter the full purchase amount")
    now=datetime.now(timezone.utc); iid=ObjectId(); dt=_date(body.purchase_date)
    existing=await db.investment_transactions.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
    if existing: return {"id":existing.get("investment_id"),"duplicate":True}
    async def op(session):
        tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)},session=session)
        if not tenant: raise HTTPException(404,"Group not found")
        # Serialize purchases per group/account so concurrent purchases cannot both spend the same snapshot balance.
        await db.investment_account_locks.update_one({"tenant_id":tenant_id,"account":body.account},{"$inc":{"version":1},"$setOnInsert":{"created_at":now}},upsert=True,session=session)
        balance=await _account_balance(session,tenant_id,body.account,tenant)
        if balance < paid: raise HTTPException(400,f"Insufficient {body.account} balance. Available ₹{float(from_minor(balance)):,.2f}")
        doc={"_id":iid,"tenant_id":tenant_id,"name":body.name.strip(),"category":body.category,"description":body.description,"purchase_date":dt,"original_amount_minor":amount,"capital_returned_minor":0,"remaining_capital_minor":amount,"current_value_minor":to_minor(body.current_estimated_value if body.current_estimated_value is not None else body.purchase_amount),"quantity":body.quantity,"vendor":body.vendor,"reference_number":body.reference_number,"notes":body.notes,"maturity_date":_date(body.maturity_date) if body.maturity_date else None,"expected_return_minor":to_minor(body.expected_return or 0),"status":"active","realized_profit_minor":0,"realized_loss_minor":0,"created_by":str(user.get("_id")),"created_at":now,"updated_at":now}
        await db.group_investments.insert_one(doc,session=session)
        await db.investment_transactions.insert_one({"tenant_id":tenant_id,"investment_id":iid,"type":"purchase","amount_minor":amount,"capital_minor":amount,"profit_minor":0,"loss_minor":0,"account":body.account,"date":dt,"notes":body.notes,"idempotency_key":body.idempotency_key,"created_by":str(user.get("_id")),"created_at":now},session=session)
        txid=await _post_money(session,tenant_id=tenant_id,actor=user,amount_minor=-amount,account=body.account,typ="investment_disbursement",investment_id=iid,note=f"Investment purchase: {body.name}",date_value=dt,idempotency_key=f"investment-purchase:{body.idempotency_key}",capital_minor=amount)
        await db.investment_transactions.update_one({"tenant_id":tenant_id,"investment_id":iid,"idempotency_key":body.idempotency_key},{"$set":{"transaction_id":str(txid)}},session=session)
        return {"id":str(iid),"transaction_id":str(txid)}
    return await run_in_transaction(db.client,op)

@router.post("/group/{tenant_id}/investments/{investment_id}/returns")
async def return_investment(tenant_id: str, investment_id: str, body: InvestmentReturn, user=Depends(current_user)):
    if user.get("role") not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    await tenant_guard(user,tenant_id); db=get_db(); iid=parse_oid(investment_id); now=datetime.now(timezone.utc); dt=_date(body.transaction_date)
    existing=await db.investment_transactions.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
    if existing: return {"id":str(existing.get("investment_id")),"duplicate":True}
    async def op(session):
        inv=await db.group_investments.find_one({"_id":iid,"tenant_id":tenant_id},session=session)
        if not inv: raise HTTPException(404,"Investment not found")
        if inv.get("status")!="active": raise HTTPException(409,"Investment is already closed")
        remaining=int(inv.get("remaining_capital_minor",0)); capital=int(body.capital_return_minor); proceeds=int(body.proceeds_minor)
        if capital<=0 or capital>remaining: raise HTTPException(400,"Capital recovery must be positive and cannot exceed remaining invested capital")
        profit=max(0,proceeds-capital); loss=max(0,capital-proceeds)
        tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)},session=session)
        balance=await _account_balance(session,tenant_id,body.account,tenant or {})
        if body.account not in ("cash","bank"): raise HTTPException(400,"Invalid receiving account")
        new_remaining=remaining-capital
        if body.close and new_remaining != 0:
            raise HTTPException(400,"Recover all remaining capital before closing this investment")
        status="closed" if new_remaining==0 else "active"
        current_value=int(inv.get("current_value_minor",remaining) or 0)
        value_reduction=current_value if new_remaining==0 else min(current_value, round(current_value*capital/max(1,remaining)))
        await db.investment_transactions.insert_one({"tenant_id":tenant_id,"investment_id":iid,"type":"return" if status=="active" else "sale","amount_minor":proceeds,"capital_minor":capital,"profit_minor":profit,"loss_minor":loss,"value_reduction_minor":value_reduction,"account":body.account,"date":dt,"notes":body.notes,"idempotency_key":body.idempotency_key,"created_by":str(user.get("_id")),"created_at":now},session=session)
        await db.group_investments.update_one({"_id":iid,"tenant_id":tenant_id,"remaining_capital_minor":{"$gte":capital}},{"$inc":{"capital_returned_minor":capital,"remaining_capital_minor":-capital,"current_value_minor":-value_reduction,"realized_profit_minor":profit,"realized_loss_minor":loss},"$set":{"status":status,"updated_at":now}},session=session)
        txid=await _post_money(session,tenant_id=tenant_id,actor=user,amount_minor=proceeds,account=body.account,typ="investment_return",investment_id=iid,note=body.notes or f"Investment return: {inv.get('name')}",date_value=dt,idempotency_key=f"investment-return:{body.idempotency_key}",capital_minor=capital,profit_minor=profit,loss_minor=loss)
        await db.investment_transactions.update_one({"tenant_id":tenant_id,"investment_id":iid,"idempotency_key":body.idempotency_key},{"$set":{"transaction_id":str(txid)}},session=session)
        return {"id":investment_id,"transaction_id":str(txid),"capital_minor":capital,"profit_minor":profit,"loss_minor":loss,"remaining_capital_minor":new_remaining,"status":status}
    return await run_in_transaction(db.client,op)

@router.patch("/group/{tenant_id}/investments/{investment_id}")
async def update_investment(tenant_id: str, investment_id: str, body: InvestmentUpdate, user=Depends(current_user)):
    if user.get("role") not in ("group_admin", "super_admin"):
        raise HTTPException(403, "Admin access required")
    await tenant_guard(user, tenant_id)
    db = get_db(); iid = parse_oid(investment_id); now = datetime.now(timezone.utc)
    update = body.model_dump()
    update["maturity_date"] = _date(body.maturity_date) if body.maturity_date else None
    update["expected_return_minor"] = to_minor(body.expected_return or 0)
    update.pop("expected_return", None)
    update["updated_at"] = now
    result = await db.group_investments.update_one({"_id": iid, "tenant_id": tenant_id, "status": {"$in": ["active", "closed"]}}, {"$set": update})
    if not result.matched_count:
        raise HTTPException(404, "Investment not found or reversed")
    await db.audit_logs.insert_one({"tenant_id":tenant_id,"actor_id":str(user.get("_id")),"action":"INVESTMENT_METADATA_UPDATED","entity":"investment","entity_id":investment_id,"details":{"fields":list(update.keys())},"created_at":now})
    return {"id":investment_id,"updated":True}

@router.post("/group/{tenant_id}/investments/{investment_id}/transactions/{investment_transaction_id}/reverse")
async def reverse_investment_transaction(tenant_id: str, investment_id: str, investment_transaction_id: str, user=Depends(current_user)):
    """Reverse only the latest monetary event for an investment; preserve its audit trail."""
    if user.get("role") not in ("group_admin", "super_admin"):
        raise HTTPException(403, "Admin access required")
    await tenant_guard(user, tenant_id)
    db=get_db(); iid=parse_oid(investment_id); itid=parse_oid(investment_transaction_id); now=datetime.now(timezone.utc)
    async def op(session):
        inv=await db.group_investments.find_one({"_id":iid,"tenant_id":tenant_id},session=session)
        source=await db.investment_transactions.find_one({"_id":itid,"tenant_id":tenant_id,"investment_id":iid},session=session)
        if not inv or not source: raise HTTPException(404,"Investment transaction not found")
        if source.get("type") not in ("purchase","return","sale","interest"):
            raise HTTPException(409,"Only purchase, return/sale or received-interest entries can be reversed here")
        prior_reversal=await db.investment_transactions.find_one({"tenant_id":tenant_id,"reversal_of":itid},session=session)
        if source.get("reversed_at") or prior_reversal:
            return {"id":str(iid),"reversal_id":str(prior_reversal.get("_id")) if prior_reversal else None,"reversed":True,"duplicate":True}
        # A later monetary event or valuation makes a rollback ambiguous. Reverse those first.
        source_created=source.get("created_at",datetime.min.replace(tzinfo=timezone.utc))
        later_order={"$or":[{"created_at":{"$gt":source_created}},{"created_at":source_created,"_id":{"$gt":itid}}]}
        later_money=await db.investment_transactions.find_one({"tenant_id":tenant_id,"investment_id":iid,"_id":{"$ne":itid},"type":{"$ne":"reversal"},"reversed_at":{"$exists":False},**later_order},session=session)
        later_value=await db.investment_valuations.find_one({"tenant_id":tenant_id,"investment_id":iid,**later_order},session=session)
        if later_money:
            raise HTTPException(409,"Reverse later monetary investment activity first")
        txid_ref=source.get("transaction_id")
        tx=await db.transactions.find_one({"_id":parse_oid(txid_ref),"tenant_id":tenant_id,"investment_id":str(iid)},session=session) if txid_ref else None
        # Legacy records did not persist the linked transaction id; use strict source attributes.
        if not tx:
            expected_type={"purchase":"investment_disbursement","return":"investment_return","sale":"investment_return","interest":"interest"}[source["type"]]
            expected_amount=-int(source.get("amount_minor",0)) if source["type"]=="purchase" else int(source.get("amount_minor",0))
            tx=await db.transactions.find_one({"tenant_id":tenant_id,"investment_id":str(iid),"type":expected_type,"amount_minor":expected_amount,"date":source.get("date")},session=session)
        if not tx: raise HTTPException(409,"Linked cash/bank transaction was not found; manual reconciliation is required")
        if await db.transactions.find_one({"tenant_id":tenant_id,"reversal_of":str(tx["_id"])},session=session):
            raise HTTPException(409,"Linked cash/bank transaction is already reversed")
        journal=await db.journal_entries.find_one({"tenant_id":tenant_id,"source_key":f"tx:{tx['_id']}","status":"posted","balanced":True},session=session)
        if not journal: raise HTTPException(409,"Original posted journal is missing; reconcile before reversal")
        inverse=reverse_journal(journal)
        reversal_id=ObjectId(); signed=-int(tx.get("amount_minor",0)); amount=float(from_minor(signed))
        reversal={"_id":reversal_id,"tenant_id":tenant_id,"member_id":None,"type":"reversal","original_type":tx.get("type"),"reversal_of":str(tx["_id"]),"amount":amount,"amount_minor":signed,"account":tx.get("account","cash"),"date":now,"created_at":now,"created_by":str(user.get("_id")),"created_by_role":user.get("role"),"note":f"Investment reversal: {inv.get('name')}","investment_id":str(iid),"idempotency_key":f"investment-reversal:{tx['_id']}","investment_realized_profit_minor":tx.get("investment_realized_profit_minor",0),"investment_realized_loss_minor":tx.get("investment_realized_loss_minor",0),"side_effects_status":"complete","journal_status":"posted"}
        await db.transactions.insert_one(reversal,session=session)
        await db.journal_entries.insert_one({"tenant_id":tenant_id,"source_type":"transaction","source_id":str(reversal_id),"source_key":f"tx:{reversal_id}",**inverse,"status":"posted","created_at":now,"posted_at":now},session=session)
        await db.financial_feed.insert_one({"source_key":f"tx:{reversal_id}","source_type":"transaction","source_id":str(reversal_id),"tenant_id":tenant_id,"member_id":None,"type":"reversal","original_type":tx.get("type"),"reversal_of":str(tx["_id"]),"amount":amount,"amount_minor":signed,"account":tx.get("account","cash"),"date":now,"created_at":now,"note":reversal["note"],"investment_id":str(iid),"idempotency_key":f"investment-reversal:{tx['_id']}","investment_realized_profit_minor":tx.get("investment_realized_profit_minor",0),"investment_realized_loss_minor":tx.get("investment_realized_loss_minor",0)},session=session)
        typ=source["type"]; capital=int(source.get("capital_minor",0)); profit=int(source.get("profit_minor",0)); loss=int(source.get("loss_minor",0)); amount_minor=int(source.get("amount_minor",0))
        if typ=="purchase":
            if int(inv.get("capital_returned_minor",0)) or int(inv.get("remaining_capital_minor",0))!=int(inv.get("original_amount_minor",0)):
                raise HTTPException(409,"Purchase can only be reversed before any capital is returned")
            await db.group_investments.update_one({"_id":iid,"tenant_id":tenant_id},{"$set":{"remaining_capital_minor":0,"current_value_minor":0,"status":"reversed","reversed_at":now,"updated_at":now}},session=session)
        elif typ in ("return","sale"):
            # A later valuation is an independent estimate, not a ledger movement.
            # Preserve that latest estimate while restoring the acquisition cost basis.
            value_restore=0 if later_value else int(source.get("value_reduction_minor",capital))
            await db.group_investments.update_one({"_id":iid,"tenant_id":tenant_id},{"$inc":{"capital_returned_minor":-capital,"remaining_capital_minor":capital,"current_value_minor":value_restore,"realized_profit_minor":-profit,"realized_loss_minor":-loss},"$set":{"status":"active","updated_at":now}},session=session)
        elif typ=="interest":
            await db.group_investments.update_one({"_id":iid,"tenant_id":tenant_id,"interest_received_minor":{"$gte":amount_minor}},{"$inc":{"interest_received_minor":-amount_minor},"$set":{"updated_at":now}},session=session)
        await db.investment_transactions.update_one({"_id":itid,"tenant_id":tenant_id},{"$set":{"reversed_at":now,"reversed_by":str(user.get("_id"))}},session=session)
        await db.investment_transactions.insert_one({"tenant_id":tenant_id,"investment_id":iid,"type":"reversal","amount_minor":amount_minor,"capital_minor":capital,"profit_minor":profit,"loss_minor":loss,"account":tx.get("account","cash"),"date":now,"notes":f"Reversal of {typ}","reversal_of":itid,"created_by":str(user.get("_id")),"created_at":now},session=session)
        await db.audit_logs.insert_one({"tenant_id":tenant_id,"actor_id":str(user.get("_id")),"action":"INVESTMENT_TRANSACTION_REVERSED","entity":"investment","entity_id":str(iid),"details":{"investment_transaction_id":str(itid),"transaction_id":str(tx["_id"]),"reversal_id":str(reversal_id)},"created_at":now},session=session)
        return {"id":str(iid),"reversal_id":str(reversal_id),"reversed":True}
    return await run_in_transaction(db.client,op)

@router.post("/group/{tenant_id}/investments/{investment_id}/interest")
async def record_investment_interest(tenant_id: str, investment_id: str, body: InvestmentInterest, user=Depends(current_user)):
    if user.get("role") not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    await tenant_guard(user,tenant_id); db=get_db(); iid=parse_oid(investment_id); now=datetime.now(timezone.utc); amount=to_minor(body.amount); dt=_date(body.transaction_date)
    existing=await db.investment_transactions.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
    if existing: return {"id":investment_id,"duplicate":True}
    async def op(session):
        inv=await db.group_investments.find_one({"_id":iid,"tenant_id":tenant_id,"status":"active"},session=session)
        if not inv: raise HTTPException(404,"Active investment not found")
        await db.investment_transactions.insert_one({"tenant_id":tenant_id,"investment_id":iid,"type":"interest","amount_minor":amount,"capital_minor":0,"profit_minor":amount,"loss_minor":0,"account":body.account,"date":dt,"notes":body.notes,"idempotency_key":body.idempotency_key,"created_by":str(user.get("_id")),"created_at":now},session=session)
        await db.group_investments.update_one({"_id":iid,"tenant_id":tenant_id},{"$inc":{"interest_received_minor":amount},"$set":{"updated_at":now}},session=session)
        txid=await _post_money(session,tenant_id=tenant_id,actor=user,amount_minor=amount,account=body.account,typ="interest",investment_id=iid,note=body.notes or f"Investment interest: {inv.get('name')}",date_value=dt,idempotency_key=f"investment-interest:{body.idempotency_key}")
        await db.investment_transactions.update_one({"tenant_id":tenant_id,"investment_id":iid,"idempotency_key":body.idempotency_key},{"$set":{"transaction_id":str(txid)}},session=session)
        return {"id":investment_id,"transaction_id":str(txid),"interest_minor":amount}
    return await run_in_transaction(db.client,op)

@router.post("/group/{tenant_id}/investments/{investment_id}/valuation")
async def update_valuation(tenant_id: str, investment_id: str, body: ValuationUpdate, user=Depends(current_user)):
    if user.get("role") not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    await tenant_guard(user,tenant_id); db=get_db(); iid=parse_oid(investment_id); now=datetime.now(timezone.utc); value=to_minor(body.estimated_value)
    async def op(session):
        inv=await db.group_investments.find_one_and_update({"_id":iid,"tenant_id":tenant_id,"status":"active"},{"$set":{"current_value_minor":value,"updated_at":now}},return_document=ReturnDocument.AFTER,session=session)
        if not inv: raise HTTPException(404,"Active investment not found")
        await db.investment_valuations.insert_one({"tenant_id":tenant_id,"investment_id":iid,"value_minor":value,"date":_date(body.valuation_date),"notes":body.notes,"created_by":str(user.get("_id")),"created_at":now},session=session)
        await db.audit_logs.insert_one({"tenant_id":tenant_id,"actor_id":str(user.get("_id")),"action":"INVESTMENT_VALUATION_UPDATED","entity":"investment","entity_id":investment_id,"details":{"value_minor":value},"created_at":now},session=session)
        return {"id":investment_id,"current_value_minor":value}
    return await run_in_transaction(db.client,op)

@router.post("/group/{tenant_id}/investments/{investment_id}/documents")
async def upload_investment_document(tenant_id: str, investment_id: str, file: UploadFile = File(...), user=Depends(current_user)):
    if user.get("role") not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    await tenant_guard(user,tenant_id); db=get_db(); iid=parse_oid(investment_id)
    inv=await db.group_investments.find_one({"_id":iid,"tenant_id":tenant_id})
    if not inv: raise HTTPException(404,"Investment not found")
    allowed={"application/pdf","image/jpeg","image/png","image/webp"}
    if file.content_type not in allowed: raise HTTPException(415,"Only PDF, JPG, PNG or WebP documents are allowed")
    data=await file.read()
    if not data or len(data)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"Document is empty or exceeds the upload size limit")
    now=datetime.now(timezone.utc); doc_id=ObjectId(); doc={"_id":doc_id,"tenant_id":tenant_id,"investment_id":iid,"filename":Path(file.filename or "document").name,"content_type":file.content_type,"size_bytes":len(data),"created_by":str(user.get("_id")),"created_at":now}
    if settings.database_mode.lower()=="local":
        folder=Path(settings.upload_dir).resolve()/"investments"/tenant_id/str(iid); folder.mkdir(parents=True,exist_ok=True)
        suffix=Path(file.filename or "").suffix[:10]; path=folder/f"{uuid.uuid4().hex}{suffix}"; path.write_bytes(data)
        doc.update({"storage":"local","path":str(path)})
    else:
        from app.core.cloudinary import upload_bytes
        asset=upload_bytes(data,public_id=f"investment-{uuid.uuid4().hex}",folder=f"bharat-bachat/tenants/{tenant_id}/investments/{investment_id}",resource_type="raw" if file.content_type=="application/pdf" else "image")
        doc.update({"storage":"cloudinary","url":asset.get("secure_url"),"public_id":asset.get("public_id")})
    await db.investment_documents.insert_one(doc)
    await db.audit_logs.insert_one({"tenant_id":tenant_id,"actor_id":str(user.get("_id")),"action":"INVESTMENT_DOCUMENT_UPLOADED","entity":"investment","entity_id":investment_id,"details":{"document_id":str(doc_id),"filename":doc["filename"]},"created_at":now})
    return {"id":str(doc_id),"filename":doc["filename"],"url":doc.get("url"),"download_url":f"/api/group/{tenant_id}/investments/{investment_id}/documents/{doc_id}" if doc.get("storage")=="local" else doc.get("url")}

@router.get("/group/{tenant_id}/investments/{investment_id}/documents/{document_id}")
async def get_investment_document(tenant_id: str, investment_id: str, document_id: str, user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db(); iid=parse_oid(investment_id); did=parse_oid(document_id)
    doc=await db.investment_documents.find_one({"_id":did,"tenant_id":tenant_id,"investment_id":iid})
    if not doc: raise HTTPException(404,"Document not found")
    if doc.get("storage")=="cloudinary": return {"url":doc.get("url")}
    path=Path(doc.get("path", "")).resolve()
    root=(Path(settings.upload_dir).resolve()/"investments").resolve()
    if root not in path.parents or not path.is_file(): raise HTTPException(404,"Document file not found")
    return FileResponse(path,media_type=doc.get("content_type","application/octet-stream"),filename=doc.get("filename","investment-document"))

@router.post("/group/{tenant_id}/investment-categories")
async def create_category(tenant_id: str, body: CategoryCreate, user=Depends(current_user)):
    if user.get("role") not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    await tenant_guard(user,tenant_id); db=get_db(); name=body.name.strip()
    await db.investment_categories.update_one({"tenant_id":tenant_id,"name_lower":name.lower()},{"$setOnInsert":{"tenant_id":tenant_id,"name":name,"name_lower":name.lower(),"created_at":datetime.now(timezone.utc),"created_by":str(user.get("_id"))}},upsert=True)
    return {"name":name}
