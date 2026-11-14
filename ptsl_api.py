"""Authenticated two-way portal API. Run: python ptsl_api.py (127.0.0.1:8001)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from storage import ConflictError, Storage


class Verdict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    record_id: str = Field(pattern=r'^[a-f0-9]{32}:[A-Za-z0-9_-]{1,64}$')
    expected_revision: int = Field(ge=1)
    verdict: Literal['Good', 'Bad', 'Unsure']
    reviewer_role: Literal['AI_Engineer', 'Linguist', 'Project_Lead']
    edited_text: str | list[dict[str, str]] | None = None
    reviewer_notes: str = Field(default='', max_length=4000)


def webhook_config():
    url=os.getenv('PTSL_WEBHOOK_URL','').strip()
    secret=os.getenv('PTSL_WEBHOOK_SECRET','')
    if not url:return '', ''
    parsed=urlsplit(url)
    local=parsed.hostname in {'127.0.0.1','localhost','::1'}
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or (parsed.scheme!='https' and not (local and parsed.scheme=='http' and os.getenv('STUDIO_ENV')!='production')):
        raise ValueError('Webhook URL must use HTTPS, without credentials/query/fragment; localhost HTTP is development-only.')
    if len(secret)<32:raise ValueError('PTSL_WEBHOOK_SECRET must contain at least 32 characters.')
    return url,secret


def deliver_pending(store, url, secret, limit=10, sender=None):
    """At-least-once delivery: stable event ID, timestamped HMAC and persisted retries."""
    sender=sender or requests.post
    delivered=0
    for _ in range(limit):
        entry=store.claim_webhook()
        if not entry:break
        body=json.dumps(entry['payload'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        timestamp=str(int(time.time()))
        signature=hmac.new(secret.encode(),timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        try:
            response=sender(url,data=body,headers={'Content-Type':'application/json','X-WaRiSAN-Event-ID':entry['id'],'X-WaRiSAN-Signature':f't={timestamp},v1={signature}'},timeout=(3,8),allow_redirects=False)
            success=200<=response.status_code<300
        except requests.RequestException:
            success=False
        store.finish_webhook(entry['id'],entry['lease'],success)
        delivered+=int(success)
    return delivered


def create_app(store=None, token=None, allowed_roles=None):
    token=os.getenv('PTSL_API_TOKEN','') if token is None else token
    roles=set(allowed_roles if allowed_roles is not None else [r.strip() for r in os.getenv('PTSL_API_REVIEWER_ROLES','Linguist').split(',') if r.strip()])
    if not roles or not roles<={'AI_Engineer','Linguist','Project_Lead'}:raise ValueError('Invalid PTSL_API_REVIEWER_ROLES')
    bearer=HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app):
        if len(token)<32:raise ValueError('Set PTSL_API_TOKEN to a random secret of at least 32 characters before starting the API.')
        if app.state.storage is None:app.state.storage=Storage()
        url,secret=webhook_config()
        stop=asyncio.Event()
        async def worker():
            while not stop.is_set():
                await asyncio.to_thread(deliver_pending,app.state.storage,url,secret,10)
                try:await asyncio.wait_for(stop.wait(),timeout=5)
                except asyncio.TimeoutError:pass
        task=asyncio.create_task(worker()) if url else None
        try:yield
        finally:
            stop.set()
            if task:await task

    app=FastAPI(title='WaRiSAN PTSL Review API',version='1.0.0',lifespan=lifespan,docs_url=None if os.getenv('STUDIO_ENV')=='production' else '/docs',redoc_url=None)
    app.state.storage=store
    origins=[v.strip() for v in os.getenv('PTSL_ALLOWED_ORIGINS','').split(',') if v.strip()]
    if '*' in origins:raise ValueError('Use explicit portal origins; wildcard CORS is not allowed.')
    if origins:app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

    async def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if len(token)<32:raise HTTPException(503,'Portal API is not configured.')
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(),token.encode()):
            raise HTTPException(401,'Invalid bearer token.',headers={'WWW-Authenticate':'Bearer'})

    @app.middleware('http')
    async def protect_response(request:Request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    class BodyLimitMiddleware:
        def __init__(self,app):self.app=app
        async def __call__(self,scope,receive,send):
            if scope['type']=='http' and scope['method']=='POST':
                body=bytearray()
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect':return
                    body.extend(message.get('body',b''))
                    if len(body)>2*1024*1024:
                        reply=JSONResponse(status_code=413,content={'detail':'Submission exceeds 2 MiB.'},headers={'Cache-Control':'no-store'})
                        await reply(scope,receive,send)
                        return
                    if not message.get('more_body',False):break
                consumed=False
                async def replay():
                    nonlocal consumed
                    if not consumed:
                        consumed=True
                        return {'type':'http.request','body':bytes(body),'more_body':False}
                    return await receive()
                await self.app(scope,replay,send)
                return
            await self.app(scope,receive,send)
    app.add_middleware(BodyLimitMiddleware)

    @app.exception_handler(RequestValidationError)
    async def redacted_validation(request,exc):
        return JSONResponse(status_code=422,content={'detail':'Invalid submission schema. Use queue record_id/expected_revision, a supported verdict and reviewer role.'})

    @app.get('/api/ptsl/queue',dependencies=[Depends(authenticate)])
    def queue(batch_id:str | None=Query(default=None,pattern=r'^[a-f0-9]{32}$')):
        try:return {'items':app.state.storage.queue(batch_id),'status':app.state.storage.status(batch_id)}
        except KeyError:raise HTTPException(404,'Batch not found.') from None

    @app.post('/api/ptsl/submit',dependencies=[Depends(authenticate)])
    def submit(payload:Verdict | list[Verdict]):
        items=payload if isinstance(payload,list) else [payload]
        try:return app.state.storage.submit([v.model_dump() for v in items],roles)
        except ConflictError:raise HTTPException(409,'Stale task. Fetch a fresh queue and retry.') from None
        except PermissionError:raise HTTPException(403,'This credential does not grant the requested role.') from None
        except KeyError:raise HTTPException(404,'Task or batch not found.') from None
        except (ValueError,TypeError):raise HTTPException(422,'Submission rejected. Check role permissions, verification profile, conversation structure and PII placeholders. No verdicts were applied.') from None

    @app.get('/api/ptsl/status',dependencies=[Depends(authenticate)])
    def status(batch_id:str | None=Query(default=None,pattern=r'^[a-f0-9]{32}$')):
        try:return app.state.storage.status(batch_id)
        except KeyError:raise HTTPException(404,'Batch not found.') from None

    return app


app=create_app()

if __name__=='__main__':
    import uvicorn
    print('WaRiSAN PTSL API: http://127.0.0.1:8001 — bearer token required. Keep portal credentials on its backend.')
    uvicorn.run(app,host='127.0.0.1',port=8001)
