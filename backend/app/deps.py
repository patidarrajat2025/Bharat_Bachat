from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId
from .core.security import decode_token
from .db import get_db
from .share_service import ensure_group_admin_member
bearer=HTTPBearer(auto_error=False)
async def current_user_raw(credentials: HTTPAuthorizationCredentials=Depends(bearer)):
    if not credentials: raise HTTPException(status_code=401,detail="Authentication required")
    try: payload=decode_token(credentials.credentials)
    except Exception: raise HTTPException(status_code=401,detail="Invalid or expired token")
    try: uid=ObjectId(payload["sub"])
    except Exception: raise HTTPException(status_code=401,detail="Invalid session")
    user=await get_db().users.find_one({"_id":uid})
    if not user or not user.get("active",True): raise HTTPException(status_code=401,detail="User inactive or missing")
    token_changed=payload.get("pwd_changed_at"); stored=user.get("password_changed_at")
    if token_changed is not None and stored is not None:
        try:
            ts=stored.timestamp() if hasattr(stored,"timestamp") else float(stored)
            if abs(float(token_changed)-ts)>1.0: raise HTTPException(status_code=401,detail="Session expired. Please login again.")
        except HTTPException: raise
        except Exception: pass
    if user.get("role")=="group_admin":
        await ensure_group_admin_member(user)
    return user
async def current_user(user=Depends(current_user_raw)):
    if user.get("must_change_password",False): raise HTTPException(status_code=403,detail="Password change required before continuing")
    return user
def require_roles(*roles):
    async def checker(user=Depends(current_user)):
        if user["role"] not in roles: raise HTTPException(status_code=403,detail="Insufficient role")
        return user
    return checker
async def tenant_guard(user,tenant_id:str):
    if user["role"]=="super_admin": return
    if str(user.get("tenant_id"))!=tenant_id: raise HTTPException(status_code=403,detail="Cross-tenant access denied")

def parse_oid(value:str):
    try: return ObjectId(value)
    except Exception: raise HTTPException(status_code=400,detail="Invalid identifier")
