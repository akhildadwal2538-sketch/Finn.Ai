from __future__ import annotations
import re
from typing import Literal, Optional
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local", override=False)

from .services.core import (portfolio,market,companies,announcements,alerts,retrieval,analyst,DEMO,CATEGORIES,connect)

app=FastAPI(title="FINN Intelligence API",version="0.1.0",description="Evidence-aware investor intelligence prototype")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:3000","http://127.0.0.1:3000","http://localhost:3001","http://127.0.0.1:3001"],allow_methods=["*"],allow_headers=["*"])

class HoldingIn(BaseModel): symbol:str=Field(min_length=1,max_length=20); name:str=Field(min_length=1,max_length=100); sector:str=Field(default="Other",max_length=80); exchange:Literal["NS","BO"]="NS"
class AlertPrefs(BaseModel): price_enabled:bool=True; volume_enabled:bool=True; price_threshold_pct:float=Field(default=3,gt=0,le=50); volume_multiple:float=Field(default=2,gt=0,le=100); categories:list[str]=Field(default_factory=list); priorities:list[str]=Field(default_factory=list); symbols:list[str]=Field(default_factory=list); monitor_all_companies:bool=True
class ChatTurn(BaseModel): role:Literal["user","assistant"]; content:str=Field(min_length=1,max_length=2500)
class Question(BaseModel): question:str=Field(min_length=3,max_length=2000); history:list[ChatTurn]=Field(default_factory=list,max_length=8)
class IngestUrl(BaseModel): url:str
class ResultFiling(BaseModel): source:str=Field(min_length=10,max_length=2048)

@app.get("/api/health")
def health():
    try:
        with connect() as db: db.execute("SELECT 1").fetchone()
        database_status="ok"
    except Exception:
        database_status="unavailable"
    return {"status":"ok" if database_status=="ok" else "degraded","market":"Yahoo Finance EOD adapter","announcements":"NSE public filing adapter with sample fallback","ai":"configured" if analyst.configured else "not configured","llm":analyst.configuration_status(),"database":database_status,"categories":CATEGORIES}

@app.get("/api/market")
async def get_market(range:str="1mo"):
    if range not in {"1mo","3mo","6mo","1y"}: raise HTTPException(400,"range must be 1mo, 3mo, 6mo, or 1y")
    nifty,sensex=await asyncio.gather(market.quote("^NSEI","",range),market.quote("^BSESN","",range))
    return {"nifty":nifty,"sensex":sensex}

@app.get("/api/market/quote/{symbol}")
async def get_company_quote(symbol:str,exchange:Literal["NS","BO"]="NS",range:Literal["1mo","3mo","6mo","1y"]="1y"):
    if not re.fullmatch(r"[A-Za-z0-9&.-]{1,20}",symbol): raise HTTPException(400,"symbol must be a valid NSE/BSE ticker")
    return await market.quote(symbol.upper(),exchange,range)

@app.get("/api/companies/search")
async def search_companies(q:str):
    if len(q.strip())<2: raise HTTPException(400,"Search with at least two characters")
    return {"items":await companies.search(q.strip()),"source":"Yahoo Finance symbol search"}

@app.get("/api/portfolio")
async def get_portfolio():
    rows=portfolio.list(); limit=asyncio.Semaphore(5)
    async def quote(h):
        async with limit: return await market.quote(h["symbol"],h["exchange"])
    quotes=await asyncio.gather(*(quote(h) for h in rows)); results=[{**h,**q} for h,q in zip(rows,quotes)]
    return {"items":results,"count":len(results)}

@app.post("/api/portfolio")
def add_holding(body:HoldingIn):
    portfolio.add(body.symbol,body.name,body.sector,body.exchange.upper()); return {"status":"saved","item":body.model_dump()}

@app.delete("/api/portfolio/{symbol}")
def delete_holding(symbol:str):
    if not portfolio.remove(symbol): raise HTTPException(404,"Holding not found")
    return {"status":"removed","symbol":symbol.upper()}

@app.get("/api/announcements")
async def get_announcements(window:str="24h",priority:Optional[str]=None,category:Optional[str]=None,symbol:Optional[str]=None,from_date:Optional[str]=None,to_date:Optional[str]=None,demo:bool=False):
    if window not in {"24h","7d","30d","all","custom"}: raise HTTPException(400,"window must be 24h, 7d, 30d, all, or custom")
    if window=="custom":
        if not from_date or not to_date: raise HTTPException(400,"custom window requires from_date and to_date in YYYY-MM-DD format")
        from datetime import date
        try: start=date.fromisoformat(from_date); end=date.fromisoformat(to_date)
        except ValueError: raise HTTPException(400,"custom dates must use YYYY-MM-DD format")
        if end < start: raise HTTPException(400,"to_date must be on or after from_date")
    if demo:
        items=announcements.list(window,from_date=from_date,to_date=to_date)
        items=[a for a in items if (not priority or a["priority"]==priority) and (not category or a["category"]==category) and (not symbol or a["symbol"]==symbol.upper())]
        return {"items":items,"status":"sample","source":"Illustrative sample feed; not current company events","updated_at":datetime.now(timezone.utc).isoformat(),"window":window}
    result=await announcements.list_for_portfolio(portfolio.list(),window,from_date,to_date)
    items=[a for a in result["items"] if (not priority or a["priority"]==priority) and (not category or a["category"]==category) and (not symbol or a["symbol"]==symbol.upper())]
    return {**result,"items":items,"window":window}

@app.post("/api/announcements/highlights")
async def result_highlights(body:ResultFiling):
    try: return await announcements.extract_result_highlights(body.source)
    except ValueError as e: raise HTTPException(400,str(e))
    except Exception as e: raise HTTPException(502,f"Result filing could not be parsed: {str(e)[:140]}")

@app.get("/api/alerts")
def get_alerts(): return alerts.get()

@app.put("/api/alerts")
def put_alerts(body:AlertPrefs): return alerts.put(body.model_dump())

@app.post("/api/alerts/evaluate")
async def evaluate_alerts():
    holdings=portfolio.list(); filing_result=await announcements.list_for_portfolio(holdings,"24h")
    return {"events":await alerts.evaluate(holdings,filing_result["items"]),"preferences":alerts.get(),"announcement_status":filing_result["status"]}

@app.get("/api/research/{symbol}")
def get_research(symbol:str):
    docs=retrieval.summaries(symbol.upper())
    dossier=retrieval.dossier(symbol.upper())
    return {"symbol":symbol.upper(),"status":"primary sources indexed" if docs else "unavailable","documents":docs,"dossier":dossier,"sections":["Business snapshot","Financial snapshot","Financial trajectory","Balance sheet analysis","Cash flow and cash conversion","Management narrative vs actual performance","Management guidance","Bull vs bear"],"message":None if docs else "No primary-source documents are indexed for this company. Upload or add an official document to enable research."}

@app.post("/api/research/{symbol}/documents")
async def ingest_document(symbol:str,file:UploadFile=File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"): raise HTTPException(400,"Upload a PDF annual report, result filing, investor presentation, or transcript.")
    content=await file.read(25*1024*1024+1)
    if len(content)>25*1024*1024: raise HTTPException(413,"PDF limit is 25 MB")
    if not content.startswith(b"%PDF-"): raise HTTPException(400,"The uploaded file is not a valid PDF.")
    from pypdf import PdfReader
    import io
    try: pages=[p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages]
    except Exception as e: raise HTTPException(400,f"Could not read PDF: {str(e)[:140]}")
    result=retrieval._ingest_pages(symbol.upper(),file.filename,"uploaded primary source",pages)
    return {**result,"title":file.filename,"symbol":symbol.upper()}

@app.post("/api/research/{symbol}/documents/url")
async def ingest_url(symbol:str,body:IngestUrl):
    try: result=await retrieval.ingest_url(symbol,body.url); return {**result,"symbol":symbol.upper()}
    except ValueError as e: raise HTTPException(400,str(e))
    except Exception as e: raise HTTPException(502,f"Source could not be fetched or parsed: {str(e)[:140]}")

@app.post("/api/research/{symbol}/ask")
async def ask_research(symbol:str,body:Question):
    ticker=symbol.upper()
    prior_questions=[turn.content for turn in body.history if turn.role=="user"][-3:]
    search_query=" ".join([*prior_questions,body.question])
    docs=retrieval.search(ticker,search_query,limit=8)
    result=await analyst.ask(body.question,docs,[turn.model_dump() for turn in body.history],ticker)
    result["symbol"]=ticker
    return result

