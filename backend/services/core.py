"""Small independent intelligence services used by the FastAPI routes."""
from __future__ import annotations
import copy, ipaddress, json, os, re, socket, sqlite3, time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("FINN_DATABASE_URL", "sqlite:///./data/finn.db")
if not DATABASE_URL.startswith("sqlite:///"):
    raise RuntimeError("FINN_DATABASE_URL currently supports SQLite paths only (sqlite:///...)")
if os.getenv("VERCEL") == "1":
    # Serverless function bundles are read-only; /tmp is writable but instance-local.
    DB_PATH = Path(os.getenv("FINN_DATABASE_PATH", "/tmp/finn.db"))
else:
    DB_PATH = Path(DATABASE_URL.removeprefix("sqlite:///"))
    if not DB_PATH.is_absolute(): DB_PATH = ROOT / DB_PATH
DEMO = json.loads((ROOT / "data/demo.json").read_text())
CATEGORIES = ["results","M&A","fund_raise","management_change","litigation","insider_trading","earnings_call","investor_meetings","credit_rating","dividend","bonus_split","capex","press_release","postal_ballot","agm_egm","pledging","unclassified"]

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(DB_PATH); db.row_factory=sqlite3.Row
    try:
        db.execute("CREATE TABLE IF NOT EXISTS market_cache(symbol TEXT PRIMARY KEY, payload TEXT, fetched REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS announcement_cache(symbol TEXT, window TEXT, payload TEXT, fetched REAL, PRIMARY KEY(symbol, window))")
        db.execute("CREATE TABLE IF NOT EXISTS announcement_highlights(source TEXT PRIMARY KEY, payload TEXT, fetched REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS announcement_analysis(item_id TEXT PRIMARY KEY, payload TEXT, fetched REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS portfolio(symbol TEXT PRIMARY KEY, name TEXT, sector TEXT, exchange TEXT, attention TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, symbol TEXT, title TEXT, source TEXT, kind TEXT, created TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id INTEGER, symbol TEXT, text TEXT, page INTEGER, citation TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT)")
        if db.execute("SELECT count(*) FROM portfolio").fetchone()[0]==0:
            db.executemany("INSERT INTO portfolio VALUES(:symbol,:name,:sector,:exchange,:attention)",DEMO["portfolio"])
        db.commit()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

class PortfolioService:
    def list(self):
        with connect() as db: return [dict(x) for x in db.execute("SELECT * FROM portfolio ORDER BY name")]
    def add(self,symbol,name,sector="Other",exchange="NS"):
        with connect() as db: db.execute("INSERT OR REPLACE INTO portfolio VALUES(?,?,?,?,'steady')",(symbol.upper().replace('.NS','').replace('.BO',''),name,sector,exchange)); db.commit()
    def remove(self,symbol):
        with connect() as db:
            cursor=db.execute("DELETE FROM portfolio WHERE symbol=?",(symbol.upper(),)); db.commit()
            return cursor.rowcount>0

class MarketDataService:
    def __init__(self): self.ttl=int(os.getenv("FINN_MARKET_CACHE_MINUTES","60"))*60
    async def quote(self,symbol,exchange="NS",period="1mo"):
        if period not in {"1mo","3mo","6mo","1y"}: period="1mo"
        yahoo=f"{symbol}.{exchange}" if exchange else symbol
        cache_symbol=yahoo if period=="1mo" else f"{yahoo}::{period}"
        with connect() as db:
            row=db.execute("SELECT payload,fetched FROM market_cache WHERE symbol=?",(cache_symbol,)).fetchone()
            if row and time.time()-row["fetched"]<self.ttl and "history" in json.loads(row["payload"]):
                return {**json.loads(row["payload"]),"status":"cached","updated_at":datetime.fromtimestamp(row["fetched"],timezone.utc).isoformat()}
        try:
            async with httpx.AsyncClient(timeout=8,headers={"User-Agent":"Mozilla/5.0 FINN research prototype"}) as client:
                res=await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}",params={"range":period,"interval":"1d"}); res.raise_for_status(); result=res.json()["chart"]["result"][0]
            meta=result.get("meta",{}); q=result["indicators"]["quote"][0]; closes=[x for x in q.get("close",[]) if x is not None]; vols=[x for x in q.get("volume",[]) if x is not None]
            price=closes[-1] if closes else None; previous=closes[-2] if len(closes)>1 else meta.get("chartPreviousClose")
            change=(price/previous-1)*100 if price and previous else None
            timestamps=result.get("timestamp",[]); history=[{"date":datetime.fromtimestamp(ts,timezone.utc).strftime("%b %d"),"value":float(close)} for ts,close in zip(timestamps,q.get("close",[])) if close is not None]
            volume_history=[{"date":datetime.fromtimestamp(ts,timezone.utc).strftime("%b %d"),"value":int(volume)} for ts,volume in zip(timestamps,q.get("volume",[])) if volume is not None]
            payload={"price":price,"change_pct":change,"volume":vols[-1] if vols else None,"avg_volume_20d":sum(vols[-21:-1])/len(vols[-21:-1]) if len(vols)>1 else None,"market_cap":meta.get("marketCap"),"currency":meta.get("currency","INR"),"history":history,"volume_history":volume_history,"source":"Yahoo Finance chart API","status":"live"}
            with connect() as db: db.execute("INSERT OR REPLACE INTO market_cache VALUES(?,?,?)",(cache_symbol,json.dumps(payload),time.time())); db.commit()
            return {**payload,"updated_at":datetime.now(timezone.utc).isoformat()}
        except Exception as e:
            if row: return {**json.loads(row["payload"]),"status":"cached","updated_at":datetime.fromtimestamp(row["fetched"],timezone.utc).isoformat()}
            return {"price":None,"change_pct":None,"volume":None,"avg_volume_20d":None,"market_cap":None,"source":"Yahoo Finance","status":"unavailable","updated_at":None,"error":str(e)[:180]}

class CompanySearchService:
    async def search(self,query):
        try:
            async with httpx.AsyncClient(timeout=8,headers={"User-Agent":"Mozilla/5.0 FINN research prototype"}) as client:
                response=await client.get("https://query1.finance.yahoo.com/v1/finance/search",params={"q":query,"quotesCount":8,"newsCount":0}); response.raise_for_status(); rows=response.json().get("quotes",[])
            matches=[]
            for row in rows:
                ticker=row.get("symbol",""); exchange="NS" if ticker.endswith(".NS") else "BO" if ticker.endswith(".BO") else None
                if exchange and row.get("quoteType")=="EQUITY": matches.append({"symbol":ticker.rsplit(".",1)[0],"exchange":exchange,"name":row.get("shortname") or row.get("longname") or ticker,"sector":"Listed company","status":"lookup"})
            return matches
        except Exception: return []

class AnnouncementClassificationService:
    def classify(self,title):
        t=title.lower(); rules={"results":["result","financial results","quarterly results","audited financial"],"management_change":["resign","resignation","appointment","appointed","ceo","cfo","managing director","director retires","change in director","change in management","re-appointment"],"M&A":["acquisition","merger","demerger","joint venture","subsidiary stake","divestment"],"fund_raise":["qip","fund rais","preferential","rights issue","commercial paper","buyback","private placement"],"litigation":["court","fine","penalty","regulatory order","show cause","litigation","insolvency","restructuring"],"insider_trading":["insider","promoter bought","promoter sold","shareholding disclosure"],"earnings_call":["concall","earnings call","conference call","transcript","recording link"],"credit_rating":["credit rating","rating rationale","rating agency"],"dividend":["dividend","record date"],"bonus_split":["bonus issue","stock split","split of face value"],"capex":["capex","capital expenditure","capacity expansion","new plant","new facility"],"investor_meetings":["investor meeting","investor meet","investor conference","analyst meet"],"pledging":["pledge","encumbrance"],"agm_egm":["agm","egm","annual general meeting","extraordinary general meeting","shareholders meeting"],"postal_ballot":["postal ballot"],"press_release":["press release","order win","orders/contracts","order received","bagging","product launch","partnership","rumour clarification"]}
        for category,terms in rules.items():
            if any(x in t for x in terms): return category
        return "unclassified"
    def priority(self,category,title=""):
        if category in {"results","M&A","litigation","management_change","fund_raise"}: return "high"
        if category in {"insider_trading","credit_rating","pledging","capex"}: return "medium"
        if category=="unclassified": return "low"
        return "medium" if category in {"dividend","bonus_split"} else "low"
    def materiality(self,category,title=""):
        base={"high":82,"medium":56,"low":22}[self.priority(category,title)]
        title=title.lower()
        if any(term in title for term in ("ceo","cfo","resignation","penalty","fine","acquisition","merger","qip")): base+=8
        return min(100,base)

class AnnouncementService:
    def __init__(self): self.classifier=AnnouncementClassificationService(); self.ttl=int(os.getenv("FINN_ANNOUNCEMENT_CACHE_MINUTES","15"))*60
    def list(self,window="24h",priority=None,category=None,symbol=None,from_date=None,to_date=None):
        local_tz=timezone(timedelta(hours=5,minutes=30)); now=datetime.now(local_tz)
        if window=="custom" and from_date and to_date:
            start=datetime.strptime(from_date,"%Y-%m-%d").replace(tzinfo=local_tz)
            end=datetime.strptime(to_date,"%Y-%m-%d").replace(hour=23,minute=59,second=59,tzinfo=local_tz)
        else:
            hours={"24h":24,"7d":168,"30d":720,"all":100000}.get(window,24); start=now-timedelta(hours=hours); end=now
        values=[]
        for index,source in enumerate(DEMO["announcements"]):
            a={**source}; a["category"]=a.get("category") or self.classifier.classify(a["title"]); a["priority"]=self.classifier.priority(a["category"],a["title"])
            if a.get("status")=="sample": a["date"]=(now-timedelta(hours=1+index*2)).isoformat()
            a["materiality_score"]=self.classifier.materiality(a["category"],a["title"]); a["summary_mode"]="sample" if a.get("status")=="sample" else "filing text"
            try:
                event_date=datetime.fromisoformat(a["date"])
                if event_date.tzinfo is None: event_date=event_date.replace(tzinfo=local_tz)
                if event_date<start or event_date>end: continue
            except ValueError: pass
            if priority and a["priority"]!=priority: continue
            if category and a["category"]!=category: continue
            if symbol and a["symbol"]!=symbol.upper(): continue
            values.append(a)
        rank={"high":0,"medium":1,"low":2}; return sorted(values,key=lambda a:(rank.get(a["priority"],3),-datetime.fromisoformat(a["date"]).timestamp()))
    async def list_for_portfolio(self,holdings,window="24h",from_date=None,to_date=None):
        """Fetch symbol-filtered disclosures from NSE; use labeled examples only if NSE is unavailable."""
        days={"24h":1,"7d":7,"30d":30,"all":365}.get(window,1)
        now=datetime.now(timezone(timedelta(hours=5,minutes=30)))
        try: start_date=datetime.strptime(from_date,"%Y-%m-%d").replace(tzinfo=now.tzinfo) if window=="custom" and from_date else now-timedelta(days=days)
        except ValueError: start_date=now-timedelta(days=days)
        try: end_date=datetime.strptime(to_date,"%Y-%m-%d").replace(hour=23,minute=59,second=59,tzinfo=now.tzinfo) if window=="custom" and to_date else now
        except ValueError: end_date=now
        if start_date>end_date: start_date,end_date=end_date,start_date
        cache_window=f"custom:{start_date.date()}:{end_date.date()}" if window=="custom" else window
        start=start_date.strftime("%d-%m-%Y"); end=end_date.strftime("%d-%m-%Y")
        symbols=sorted({h["symbol"].upper() for h in holdings if h.get("exchange","NS")=="NS"})
        if not symbols: return {"items":[],"status":"unavailable","source":"NSE public filings"}
        async def load(symbol,client,semaphore):
            with connect() as db:
                cached=db.execute("SELECT payload,fetched FROM announcement_cache WHERE symbol=? AND window=?",(symbol,cache_window)).fetchone()
            if cached and time.time()-cached["fetched"]<self.ttl:
                cached_items=json.loads(cached["payload"])
                for item in cached_items: item["status"]="cached"
                return cached_items,"cached"
            async with semaphore:
                try:
                    response=await client.get("https://www.nseindia.com/api/corporate-announcements",params={"index":"equities","symbol":symbol,"from_date":start,"to_date":end})
                    response.raise_for_status(); rows=response.json()
                    if isinstance(rows,dict): rows=rows.get("data",[])
                    if not isinstance(rows,list): raise ValueError("Unexpected NSE announcement response")
                    items=[]
                    for row in rows:
                        title=(row.get("desc") or row.get("subject") or "Corporate disclosure").strip()
                        stamp=row.get("an_dt") or row.get("sort_date") or ""
                        try:
                            date=datetime.strptime(stamp,"%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=5,minutes=30))).isoformat()
                        except ValueError:
                            try: date=datetime.strptime(stamp[:10],"%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
                            except ValueError: date=now.isoformat()
                        category=self.classifier.classify(f"{title} {row.get('attchmntText','')}")
                        source=row.get("attchmntFile") or "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
                        if datetime.fromisoformat(date)<start_date or datetime.fromisoformat(date)>end_date: continue
                        items.append({"id":f"nse-{row.get('seq_id') or symbol+'-'+stamp}","company":row.get("sm_name") or symbol,"symbol":symbol,"category":category,"title":title,"date":date,"priority":self.classifier.priority(category,title),"materiality_score":self.classifier.materiality(category,title),"summary":row.get("attchmntText") or title,"summary_mode":"filing text","why":self._why(category),"status":"live","source":source,"source_name":"NSE","exchange_time":stamp})
                    with connect() as db: db.execute("INSERT OR REPLACE INTO announcement_cache VALUES(?,?,?,?)",(symbol,cache_window,json.dumps(items),time.time()))
                    return items,"live"
                except Exception:
                    return [],"error"
        limits=__import__("asyncio").Semaphore(5)
        async with httpx.AsyncClient(timeout=12,headers={"User-Agent":"Mozilla/5.0 FINN financial intelligence prototype","Accept":"application/json, text/plain, */*","Referer":"https://www.nseindia.com/companies-listing/corporate-filings-announcements"},follow_redirects=True) as client:
            results=await __import__("asyncio").gather(*(load(symbol,client,limits) for symbol in symbols))
        items=[item for group,_ in results for item in group]; states=[state for _,state in results]
        if not any(state in {"live","cached"} for state in states):
            sample=self.list(window,from_date=from_date,to_date=to_date)
            for item in sample: item["status"]="sample"
            return {"items":sample,"status":"sample","source":"NSE unavailable; demonstration examples","updated_at":now.isoformat()}
        rank={"high":0,"medium":1,"low":2}
        unique={}
        for item in items: unique.setdefault(item.get("id") or item.get("source"),item)
        items=list(unique.values())
        items.sort(key=lambda a:(rank.get(a["priority"],3),-datetime.fromisoformat(a["date"]).timestamp()))
        items=await analyst.summarize_announcements(items)
        status="partial" if "error" in states or any(h.get("exchange","NS")!="NS" for h in holdings) else "cached" if all(s=="cached" for s in states) else "live"
        return {"items":items,"status":status,"source":"NSE corporate announcements","updated_at":now.isoformat()}
    async def extract_result_highlights(self,source):
        """Extract only literal headline statement rows from an official exchange PDF."""
        parsed=urlparse(source); host=(parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme!="https" or not (host=="nseindia.com" or host.endswith(".nseindia.com") or host=="bseindia.com" or host.endswith(".bseindia.com")) or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError("Result extraction accepts public HTTPS NSE/BSE filing links only.")
        try:
            addresses={row[4][0] for row in socket.getaddrinfo(host,None)}
            if not addresses or any(not ipaddress.ip_address(address.split("%",1)[0]).is_global for address in addresses):
                raise ValueError("Private or local filing hosts are not allowed.")
        except socket.gaierror as exc:
            raise ValueError("The filing host could not be verified.") from exc
        with connect() as db:
            cached=db.execute("SELECT payload,fetched FROM announcement_highlights WHERE source=?",(source,)).fetchone()
        if cached and time.time()-cached["fetched"]<24*3600:
            return {**json.loads(cached["payload"]),"status":"cached"}
        content=bytearray()
        async with httpx.AsyncClient(timeout=20,headers={"User-Agent":"Mozilla/5.0 FINN financial research prototype"},follow_redirects=False) as client:
            async with client.stream("GET",source) as response:
                if response.is_redirect: raise ValueError("Redirected filing links are not accepted; open the exchange filing and use its final HTTPS PDF link.")
                response.raise_for_status()
                if "pdf" not in response.headers.get("content-type","").lower() and not parsed.path.lower().endswith(".pdf"):
                    raise ValueError("The linked exchange filing is not a PDF document.")
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>25*1024*1024: raise ValueError("Filing PDF exceeds the 25 MB limit.")
        if not content.startswith(b"%PDF-"): raise ValueError("The exchange filing did not return a readable PDF.")
        from pypdf import PdfReader
        import io
        try: pages=[page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages]
        except Exception as exc: raise ValueError("The exchange PDF could not be parsed.") from exc
        title=parsed.path.rsplit("/",1)[-1] or "Exchange result filing"
        return self.extract_result_pages(source,pages,title)
    def extract_result_pages(self,source,pages,title="Exchange result filing"):
        highlights=[]
        for page_number,text in enumerate(pages,1):
            for fact in extraction.extract(text):
                if any(metric in {"Revenue","EBITDA","PBT","PAT"} for metric in fact["metrics"]):
                    highlights.append({**fact,"citation":f"{title}, page {page_number}","source":source})
        result={"highlights":highlights[:40],"source":source,"source_name":"NSE/BSE exchange filing","message":None if highlights else "Revenue, EBITDA, PBT, and PAT statement rows could not be verified in this PDF. Check the original exchange filing; FINN will not infer missing numbers.","status":"available" if highlights else "unavailable"}
        with connect() as db: db.execute("INSERT OR REPLACE INTO announcement_highlights VALUES(?,?,?)",(source,json.dumps(result),time.time()))
        return result
    def _why(self,category):
        return {"results":"Quarterly or annual results can change earnings expectations; compare reported performance with prior periods and management commentary.","management_change":"Leadership changes can affect execution and governance; verify the role, timing, and disclosed background in the filing.","M&A":"Transactions can change the company’s scale, risk, and capital needs; review consideration, financing, and completion conditions.","litigation":"Regulatory and legal matters may affect costs, operations, or reputation; check the disclosed exposure and next steps.","fund_raise":"New funding can change leverage or shareholder dilution; review the instrument, amount, and use of proceeds.","dividend":"A dividend affects shareholder distributions and has a record date; confirm the amount and eligibility dates.","credit_rating":"A rating action may change borrowing costs and access to funding; review the rationale and outlook.","capex":"Capital expenditure can support capacity but requires funding and execution; monitor timelines and expected returns."}.get(category,"Review the filing for changes to operations, governance, capital allocation, or shareholder rights.")

class AlertDetectionService:
    defaults={"price_enabled":True,"volume_enabled":True,"price_threshold_pct":3,"volume_multiple":2,"categories":["results","M&A","management_change","litigation"],"priorities":["high"],"symbols":[],"monitor_all_companies":True}
    def get(self):
        with connect() as db: row=db.execute("SELECT value FROM preferences WHERE key='alerts'").fetchone()
        return {**self.defaults,**json.loads(row[0])} if row else copy.deepcopy(self.defaults)
    def put(self,payload):
        data={**self.defaults,**payload}
        with connect() as db: db.execute("INSERT OR REPLACE INTO preferences VALUES('alerts',?)",(json.dumps(data),)); db.commit()
        return data
    async def evaluate(self,holdings,announcement_items=None):
        import asyncio
        market=MarketDataService(); prefs=self.get(); events=[]
        monitored=[h for h in holdings if prefs.get("monitor_all_companies",True) or h["symbol"] in prefs.get("symbols",[])]
        semaphore=asyncio.Semaphore(5)
        async def quote_for(holding):
            async with semaphore: return holding,await market.quote(holding["symbol"],holding.get("exchange","NS"))
        quotes=await asyncio.gather(*(quote_for(holding) for holding in monitored))
        for h,quote in quotes:
            if prefs.get("price_enabled",True) and quote.get("change_pct") is not None and abs(quote["change_pct"])>=prefs["price_threshold_pct"]: events.append({"symbol":h["symbol"],"kind":"price_move","value":quote["change_pct"],"threshold":prefs["price_threshold_pct"],"status":quote["status"]})
            avg=quote.get("avg_volume_20d")
            if prefs.get("volume_enabled",True) and avg and quote.get("volume") and quote["volume"]>=avg*prefs["volume_multiple"]: events.append({"symbol":h["symbol"],"kind":"volume_spike","value":quote["volume"]/avg,"threshold":prefs["volume_multiple"],"status":quote["status"]})
        symbols=set(prefs.get("symbols",[])); categories=set(prefs.get("categories",[])); priorities=set(prefs.get("priorities",[]))
        for item in announcement_items or []:
            if not prefs.get("monitor_all_companies",True) and item.get("symbol") not in symbols: continue
            if item.get("category") not in categories: continue
            if item.get("priority") not in priorities: continue
            events.append({"symbol":item["symbol"],"kind":"announcement","category":item["category"],"title":item["title"],"priority":item["priority"],"status":item.get("status","unavailable")})
        return events

class DocumentRetrievalService:
    research_sections={
        "Business snapshot":["business overview","business segment","segments","products and services","revenue mix","geography","operations"],
        "Financial snapshot":["revenue","income from operations","ebitda","profit before tax","profit after tax","net profit","margins"],
        "Financial trajectory":["year on year","year-on-year","growth","increased","declined","margin","quarter ended","previous year"],
        "Balance sheet analysis":["balance sheet","borrowings","total debt","liabilities","assets","working capital","debt equity","contingent liabilities"],
        "Cash flow and cash conversion":["cash flow","cash from operating activities","operating cash flow","free cash flow","capital expenditure","cash conversion"],
        "Management narrative vs actual performance":["management discussion","management commentary","outlook","guidance","actual","performance","expect"],
        "Management guidance":["guidance","outlook","expect","forecast","target","margin outlook","capex plan"],
        "Bull vs bear analysis":["opportunity","growth drivers","risk factors","competition","risk","contingent liability","auditor qualification","related party"]
    }
    def ingest(self,symbol,title,source,body,kind="primary_source"):
        pieces=self._chunk_text(body)
        with connect() as db:
            cur=db.execute("INSERT INTO documents(symbol,title,source,kind,created) VALUES(?,?,?,?,?)",(symbol.upper(),title,source,kind,datetime.now(timezone.utc).isoformat())); doc=cur.lastrowid
            db.executemany("INSERT INTO chunks(document_id,symbol,text,page,citation) VALUES(?,?,?,?,?)",[(doc,symbol.upper(),p,None,f"{title}, section {i+1}") for i,p in enumerate(pieces)]); db.commit()
        return {"document_id":doc,"chunks":len(pieces),"status":"indexed"}
    def _chunk_text(self,body,max_chars=1400):
        chunks=[]
        for paragraph in re.split(r"\n\s*\n",body):
            paragraph=" ".join(paragraph.split())
            if len(paragraph)<=40: continue
            while len(paragraph)>max_chars:
                boundary=paragraph.rfind(" ",0,max_chars)
                if boundary<max_chars//2: boundary=max_chars
                chunks.append(paragraph[:boundary].strip())
                paragraph=paragraph[max(0,boundary-180):].strip()
            if len(paragraph)>40: chunks.append(paragraph)
        return chunks
    async def ingest_url(self,symbol,url):
        parsed=urlparse(url)
        host=(parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme!="https" or not host or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError("Use a public HTTPS investor-relations, exchange, or Screener URL.")
        try:
            addresses={item[4][0] for item in socket.getaddrinfo(host,None)}
            if not addresses or any(not ipaddress.ip_address(address.split("%",1)[0]).is_global for address in addresses):
                raise ValueError("Private or local document hosts are not allowed.")
        except (socket.gaierror, ValueError) as exc:
            if isinstance(exc, ValueError): raise
            raise ValueError("The document host could not be verified.") from exc
        path=parsed.path.lower()
        # Explicit official issuer hosts supplement the common exchange/IR URL patterns.
        issuer_hosts={"ril.com","www.ril.com","hdfc.bank.in","www.hdfc.bank.in"}
        approved=host in issuer_hosts or host=="screener.in" or host.endswith(".screener.in") or host=="nseindia.com" or host.endswith(".nseindia.com") or host=="bseindia.com" or host.endswith(".bseindia.com") or "investor" in host or host.startswith("ir.") or "/investor" in path or "/ir/" in path
        if not approved: raise ValueError("Provide a company investor-relations, exchange, or Screener primary-source URL.")
        content=bytearray()
        async with httpx.AsyncClient(timeout=15,headers={"User-Agent":"Mozilla/5.0 FINN research prototype"},follow_redirects=False) as client:
            async with client.stream("GET",url) as response:
                if response.is_redirect: raise ValueError("Redirected document URLs are not accepted; provide the final HTTPS source URL.")
                response.raise_for_status(); content_type=response.headers.get("content-type","").lower()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>25*1024*1024: raise ValueError("Document URL exceeds the 25 MB limit.")
        if "pdf" in content_type or url.lower().split("?")[0].endswith(".pdf"):
            if not content.startswith(b"%PDF-"): raise ValueError("The source did not return a readable PDF.")
            from pypdf import PdfReader
            import io
            pages=[p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages]; title=urlparse(url).path.rsplit("/",1)[-1]
            return self._ingest_pages(symbol,title,url,pages)
        if "html" not in content_type and not bytes(content).lstrip().startswith(b"<"): raise ValueError("The source must be an HTML page or PDF document.")
        soup=BeautifulSoup(bytes(content),"html.parser"); title=soup.title.get_text(" ",strip=True) if soup.title else url; body=soup.get_text("\n",strip=True)
        return self.ingest(symbol,title,url,body)
    def _ingest_pages(self,symbol,title,url,pages):
        with connect() as db:
            cur=db.execute("INSERT INTO documents(symbol,title,source,kind,created) VALUES(?,?,?,?,?)",(symbol.upper(),title,url,"primary_source",datetime.now(timezone.utc).isoformat())); doc=cur.lastrowid
            chunks=[]
            for page,text in enumerate(pages,1):
                for piece in self._chunk_text(text): chunks.append((doc,symbol.upper(),piece,page,f"{title}, page {page}"))
            db.executemany("INSERT INTO chunks(document_id,symbol,text,page,citation) VALUES(?,?,?,?,?)",chunks); db.commit()
        return {"document_id":doc,"chunks":len(chunks),"status":"indexed"}
    def search(self,symbol,query,limit=6):
        words={w for w in re.findall(r"[a-zA-Z0-9]{3,}",query.lower())}
        with connect() as db: rows=[dict(r) for r in db.execute("SELECT text,citation,source,title,page FROM chunks JOIN documents ON documents.id=chunks.document_id WHERE chunks.symbol=?",(symbol.upper(),))]
        ranked=sorted(rows,key=lambda r:sum(2 if w in r["text"].lower().split() else int(w in r["text"].lower()) for w in words),reverse=True)
        return [r for r in ranked if any(w in r["text"].lower() for w in words)][:limit]
    def summaries(self,symbol):
        with connect() as db: return [dict(x) for x in db.execute("SELECT id,title,source,kind,created FROM documents WHERE symbol=? ORDER BY created DESC",(symbol.upper(),))]
    def dossier(self,symbol):
        with connect() as db:
            rows=[dict(r) for r in db.execute("SELECT text,citation,source,title,page FROM chunks JOIN documents ON documents.id=chunks.document_id WHERE chunks.symbol=?",(symbol.upper(),))]
        sections=[]
        for name,terms in self.research_sections.items():
            scored=[]
            for row in rows:
                lower=row["text"].lower(); score=sum(1 for term in terms if term in lower)
                if score: scored.append((score,row))
            scored.sort(key=lambda pair:pair[0],reverse=True)
            evidence=[]; seen=set()
            for _,row in scored:
                if row["citation"] in seen: continue
                seen.add(row["citation"]); evidence.append({"excerpt":row["text"][:650],"citation":row["citation"],"source":row["source"]})
                if len(evidence)==3: break
            sections.append({"name":name,"status":"evidence found" if evidence else "unavailable","evidence":evidence,"message":None if evidence else "Not available in indexed primary sources. FINN will not infer this section from outside knowledge."})
        financial_rows=[]
        for row in rows:
            for fact in extraction.extract(row["text"]): financial_rows.append({**fact,"citation":row["citation"],"source":row["source"]})
        return {"sections":sections,"financial_rows":financial_rows[:40],"source_count":len({r["source"] for r in rows}),"status":"evidence found" if rows else "unavailable"}

class FinancialDataExtractionService:
    fields={"Revenue":["revenue","income from operations"],"EBITDA":["ebitda"],"PBT":["profit before tax","pbt"],"PAT":["profit after tax","net profit","pat"]}
    period_pattern=re.compile(r"\b(?:Q[1-4]\s*(?:FY)?\s*['’]?\d{2,4}|FY\s*['’]?\d{2,4}\s*Q[1-4])\b",re.I)
    def periods(self,text):
        out=[]
        for match in self.period_pattern.finditer(text):
            raw=re.sub(r"\s+"," ",match.group().replace("’","'" )).strip().upper().replace("'","")
            a=re.fullmatch(r"Q([1-4])\s*(?:FY)?\s*(\d{2,4})",raw)
            b=re.fullmatch(r"FY\s*(\d{2,4})\s*Q([1-4])",raw)
            if a: q,fy=int(a.group(1)),int(a.group(2))
            elif b: fy,q=int(b.group(1)),int(b.group(2))
            else: continue
            if fy<100: fy+=2000
            label=f"Q{q} FY{str(fy)[-2:]}"
            if label not in out: out.append(label)
        return out
    def _number(self,value):
        try: return -float(value[1:-1].replace(",","")) if value.startswith("(") and value.endswith(")") else float(value.replace(",",""))
        except ValueError: return None
    def _comparisons(self,values,periods):
        if len(values)!=len(periods) or len(values)<2: return {}
        indexed={}
        for value,period in zip(values,periods):
            match=re.fullmatch(r"Q([1-4]) FY(\d{2})",period)
            number=self._number(value)
            if match and number is not None: indexed[(int(match.group(2)),int(match.group(1)))]=number
        if not indexed: return {}
        fy,q=max(indexed)
        current=indexed[(fy,q)]; out={"current_period":f"Q{q} FY{fy:02d}"}
        prior=(fy,q-1) if q>1 else (fy-1,4)
        year_ago=(fy-1,q)
        if prior in indexed and indexed[prior]!=0: out["qoq_pct"]=round((current/indexed[prior]-1)*100,2)
        if year_ago in indexed and indexed[year_ago]!=0: out["yoy_pct"]=round((current/indexed[year_ago]-1)*100,2)
        return out
    def extract(self,text):
        """Return literal statement rows; do not guess the period or units."""
        out=[]; seen=set(); periods=self.periods(text)
        for line in text.splitlines():
            cleaned=" ".join(line.split())
            if not cleaned or not re.search(r"\d",cleaned): continue
            lower=cleaned.lower(); labels=[label for label,terms in self.fields.items() if any(term in lower for term in terms)]
            if not labels: continue
            key=(tuple(labels),cleaned)
            if key in seen: continue
            seen.add(key)
            values=re.findall(r"(?<![A-Za-z])\(?-?\d[\d,]*(?:\.\d+)?\)?",cleaned)
            aligned_periods=periods if len(periods)==len(values) else []
            comparisons=self._comparisons(values,aligned_periods)
            out.append({"metrics":labels,"row":cleaned[:400],"values":values,"periods":aligned_periods,"comparisons":comparisons,"units":"not stated in extracted row","status":"Source row found; verify period and unit against the adjacent table headings."})
        return out

class AIAnalysisService:
    def __init__(self):
        self.base=os.getenv("FINN_LLM_BASE_URL","").rstrip("/")
        self.key=os.getenv("FINN_LLM_API_KEY","")
        self.model=os.getenv("FINN_LLM_MODEL","")
        self.provider=os.getenv("FINN_LLM_PROVIDER","openai_compatible").strip().lower()
        self.response_cache={}
    @property
    def configured(self): return bool(self.base and self.key and self.model)
    def configuration_status(self):
        missing=[name for name,value in (("FINN_LLM_BASE_URL",self.base),("FINN_LLM_API_KEY",self.key),("FINN_LLM_MODEL",self.model)) if not value]
        return {"configured":not missing,"provider":self.provider,"model":self.model or None,"missing":missing}
    async def complete(self,messages,max_tokens=1400,timeout=45):
        """Provider boundary. Current adapter accepts OpenAI-compatible chat-completions APIs."""
        if not self.configured: raise RuntimeError("LLM provider is not configured")
        if self.provider!="openai_compatible": raise RuntimeError(f"Unsupported LLM provider adapter: {self.provider}")
        url=self.base if self.base.endswith("/chat/completions") else self.base+"/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout,connect=8)) as client:
            response=await client.post(url,headers={"Authorization":"Bearer "+self.key,"Content-Type":"application/json"},json={"model":self.model,"messages":messages,"temperature":0.1,"max_tokens":max_tokens})
            response.raise_for_status()
            payload=response.json()
        content=payload.get("choices",[{}])[0].get("message",{}).get("content")
        if isinstance(content,list): content="".join(part.get("text","") for part in content if isinstance(part,dict))
        if not isinstance(content,str) or not content.strip(): raise ValueError("LLM provider returned no message content")
        return content.strip()
    async def summarize_announcements(self,items):
        if not (self.base and self.key and self.model): return items
        import asyncio
        semaphore=asyncio.Semaphore(2)
        async def enrich(item):
            if item.get("status") not in {"live","cached"}: return item
            with connect() as db: cached=db.execute("SELECT payload,fetched FROM announcement_analysis WHERE item_id=?",(item["id"],)).fetchone()
            if cached and time.time()-cached["fetched"]<24*3600:
                return {**item,**json.loads(cached["payload"]),"summary_mode":"AI","ai_cache":"cached"}
            prompt=("Use only this exchange filing metadata and filing text. Return JSON with `summary` (one sentence), `why` (one sentence about investor-relevant implications without advice), and `materiality_score` (integer 0-100). Do not add facts, figures, dates, people, or guidance not present. If the text is insufficient, say so.\n"
                    f"Category: {item.get('category')}\nTitle: {item.get('title')}\nFiling text: {str(item.get('summary',''))[:5000]}\nSource: {item.get('source')}")
            try:
                async with semaphore:
                    raw=await self.complete([{"role":"user","content":prompt}],max_tokens=600,timeout=18)
                parsed=json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
                score=int(parsed.get("materiality_score",item["materiality_score"]))
                generated={"summary":str(parsed.get("summary") or item["summary"])[:500],"why":str(parsed.get("why") or item["why"])[:700],"materiality_score":max(0,min(100,score))}
                with connect() as db: db.execute("INSERT OR REPLACE INTO announcement_analysis VALUES(?,?,?)",(item["id"],json.dumps(generated),time.time()))
                return {**item,**generated,"summary_mode":"AI","ai_cache":"live"}
            except Exception:
                return item
        candidates=[item for item in items if item.get("priority")=="high"][:4]
        enriched=await asyncio.gather(*(enrich(item) for item in candidates))
        by_id={item["id"]:item for item in enriched}
        return [by_id.get(item["id"],item) for item in items]
    async def ask(self,question,docs,history=None,symbol=""):
        ticker=symbol.upper()
        if not docs: return {"answer":"I can't verify this from indexed primary-source documents yet. Add the company's annual report, results filing, investor presentation, or earnings-call transcript to research it.","verified_facts":[],"interpretation":None,"citations":[],"confidence":"unavailable","status":"unavailable"}
        source_citations=[{"citation":d["citation"],"excerpt":d["text"][:700],"source":d["source"]} for d in docs]
        if not self.configured:
            return {"answer":"Source passages were retrieved, but the LLM is not configured yet. Add the provider, model, API endpoint, and server-side API key in `.env.local` to generate a research response.","verified_facts":[],"interpretation":None,"citations":source_citations,"confidence":"retrieval-only","status":"needs_configuration"}
        evidence="\n\n".join(f"[{d['citation']} | {d['source']}]\n{d['text']}" for d in docs)
        history=list(history or [])[-8:]
        messages=[{"role":"system","content":"You are FINN, an evidence-grounded Indian equity research assistant. Use only the supplied primary-source excerpts for company facts. Conversation history may resolve references but is not evidence. If the retrieved excerpts do not support a requested fact, explicitly say the evidence is unavailable. Never invent financial numbers, periods, units, guidance, executives, citations, or outside knowledge. Distinguish verified facts from interpretation. No personalized buy/sell recommendations. Return a JSON object with keys answer (concise Markdown), verified_facts (array of strings), interpretation (string or null), financial_implication (string), why_it_matters (string), risk_or_opportunity (string), citations (array of objects with exact citation and source from the excerpts), confidence (string). Only cite exact supplied source/citation pairs."}]
        for turn in history:
            if turn.get("role") in {"user","assistant"} and isinstance(turn.get("content"),str):
                messages.append({"role":turn["role"],"content":turn["content"][:2500]})
        messages.append({"role":"user","content":f"Selected company ticker: {ticker}.\nQuestion: {question}\n\nRetrieved primary-source excerpts:\n{evidence}"})
        cache_key=__import__("hashlib").sha256(json.dumps([self.provider,self.base,self.model,messages],sort_keys=True).encode()).hexdigest()
        cached=self.response_cache.get(cache_key)
        if cached and time.time()-cached[0]<300: return {**copy.deepcopy(cached[1]),"cache":"hit"}
        try:
            answer=await self.complete(messages,max_tokens=1400,timeout=45)
        except Exception as exc:
            return {"answer":"The configured LLM request failed. Check the provider, model, endpoint, credentials, and server logs, then retry.","verified_facts":[],"interpretation":None,"citations":source_citations,"confidence":"retrieval-only","status":"provider_error","error_type":type(exc).__name__}
        allowed={(d["citation"],d["source"]) for d in docs}
        try:
            raw=answer.strip().removeprefix("```json").removesuffix("```").strip()
            structured=json.loads(raw)
            if not isinstance(structured,dict): raise ValueError("Expected a JSON object")
            citations=[c for c in structured.get("citations",[]) if isinstance(c,dict) and (c.get("citation"),c.get("source")) in allowed]
            structured["citations"]=citations or source_citations
            structured["answer"]=str(structured.get("answer") or structured.get("observation") or "The model did not provide an answer.")[:10000]
            structured["verified_facts"]=[str(x)[:1500] for x in structured.get("verified_facts",[]) if isinstance(x,(str,int,float))][:10]
            structured["interpretation"]=str(structured["interpretation"])[:4000] if structured.get("interpretation") else None
            structured["confidence"]=str(structured.get("confidence") or "not stated")[:80]
            structured["status"]="answered"
        except (TypeError,ValueError):
            structured={"answer":answer[:10000],"verified_facts":[],"interpretation":"The model response was not structured; verify all statements against the cited source excerpts.","citations":source_citations,"confidence":"low","status":"answered_unstructured"}
        self.response_cache[cache_key]=(time.time(),copy.deepcopy(structured))
        if len(self.response_cache)>100: self.response_cache={k:v for k,v in self.response_cache.items() if time.time()-v[0]<300}
        return structured

portfolio=PortfolioService(); market=MarketDataService(); companies=CompanySearchService(); announcements=AnnouncementService(); alerts=AlertDetectionService(); retrieval=DocumentRetrievalService(); extraction=FinancialDataExtractionService(); analyst=AIAnalysisService()
