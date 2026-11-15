import unittest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.core import AIAnalysisService, AnnouncementClassificationService, AnnouncementService, AlertDetectionService, DocumentRetrievalService, FinancialDataExtractionService, connect

class IntelligenceTests(unittest.TestCase):
    def test_classifies_high_material_types(self):
        service=AnnouncementClassificationService()
        for title,expected in [("Quarterly financial results","results"),("CFO resigns","management_change"),("Acquisition of subsidiary","M&A"),("Regulatory penalty order","litigation")]:
            with self.subTest(title=title): self.assertEqual(service.classify(title),expected)
        self.assertEqual(service.priority("results"),"high")
        self.assertEqual(service.priority("unclassified"),"low")

    def test_demo_announcements_are_explicitly_sample(self):
        items=AnnouncementService().list("all")
        self.assertTrue(items)
        self.assertTrue(all(item["status"]=="sample" for item in items))

    def test_default_demo_digest_has_recent_sample_items(self):
        items=AnnouncementService().list("24h")
        self.assertTrue(items)
        self.assertTrue(all(item["status"]=="sample" for item in items))
        self.assertTrue(all((datetime.now().astimezone()-datetime.fromisoformat(item["date"]).astimezone()).total_seconds()<24*3600 for item in items))

    def test_api_demo_feed_is_explicit_and_keeps_default_digest_populated(self):
        response=TestClient(app).get("/api/announcements?window=24h&demo=true")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["status"],"sample")
        self.assertTrue(response.json()["items"])
        self.assertTrue(all(item["status"]=="sample" for item in response.json()["items"]))

    def test_nse_adapter_maps_official_filings_and_citations(self):
        symbol="FINN_NSE_ADAPTER_TEST"
        class Response:
            def raise_for_status(self): pass
            def json(self):
                from datetime import datetime, timezone, timedelta
                stamp=datetime.now(timezone(timedelta(hours=5,minutes=30))).strftime("%d-%b-%Y %H:%M:%S")
                return [{"seq_id":"123","symbol":symbol,"sm_name":"Test Listed Co","desc":"Financial Results","an_dt":stamp,"attchmntText":"Company reported financial results for the quarter.","attchmntFile":"https://nsearchives.nseindia.com/corporate/test.pdf"}]
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def get(self,*args,**kwargs): return Response()
        with patch("backend.services.core.httpx.AsyncClient",return_value=Client()):
            result=asyncio.run(AnnouncementService().list_for_portfolio([{"symbol":symbol,"exchange":"NS"}]))
        self.assertEqual(result["status"],"live")
        self.assertEqual(result["items"][0]["category"],"results")
        self.assertEqual(result["items"][0]["status"],"live")
        self.assertEqual(result["items"][0]["source"],"https://nsearchives.nseindia.com/corporate/test.pdf")
        with connect() as db: db.execute("DELETE FROM announcement_cache WHERE symbol=?",(symbol,)); db.commit()

    def test_unconfigured_alert_threshold_defaults(self):
        self.assertEqual(AlertDetectionService.defaults["price_threshold_pct"],3)
        self.assertEqual(AlertDetectionService.defaults["volume_multiple"],2)

    def test_alert_rules_respect_enabled_flags_company_category_and_priority(self):
        service=AlertDetectionService()
        prefs={"price_enabled":False,"volume_enabled":False,"price_threshold_pct":3,"volume_multiple":2,"categories":["results"],"priorities":["high"],"symbols":["AAA"],"monitor_all_companies":False}
        holdings=[{"symbol":"AAA","exchange":"NS"},{"symbol":"BBB","exchange":"NS"}]
        filings=[{"symbol":"AAA","category":"results","priority":"high","title":"Results filed","status":"sample"},{"symbol":"AAA","category":"dividend","priority":"low","title":"Routine dividend notice","status":"sample"},{"symbol":"BBB","category":"results","priority":"high","title":"Other holding results","status":"sample"}]
        with patch.object(service,"get",return_value=prefs),patch("backend.services.core.MarketDataService.quote",new_callable=AsyncMock,return_value={"change_pct":8,"volume":500,"avg_volume_20d":100,"status":"cached"}):
            events=asyncio.run(service.evaluate(holdings,filings))
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["kind"],"announcement")
        self.assertEqual(events[0]["symbol"],"AAA")

    def test_alert_preferences_api_persists_and_evaluates(self):
        client=TestClient(app); original=client.get("/api/alerts").json()
        prefs={**original,"price_threshold_pct":4.5,"volume_multiple":2.5,"categories":["results"],"priorities":["high"],"monitor_all_companies":True}
        self.assertEqual(client.put("/api/alerts",json=prefs).status_code,200)
        self.assertEqual(client.get("/api/alerts").json()["price_threshold_pct"],4.5)
        with patch("backend.main.announcements.list_for_portfolio",new_callable=AsyncMock,return_value={"items":[],"status":"sample"}),patch("backend.main.alerts.evaluate",new_callable=AsyncMock,return_value=[]):
            evaluated=client.post("/api/alerts/evaluate")
        self.assertEqual(evaluated.status_code,200)
        client.put("/api/alerts",json=original)

    def test_portfolio_api_returns_quotes_from_market_adapter(self):
        client=TestClient(app)
        with patch("backend.main.market.quote",new_callable=AsyncMock,return_value={"price":100,"change_pct":1,"history":[],"status":"live"}):
            result=client.get("/api/portfolio")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["count"],18)
        self.assertTrue(all(row["status"]=="live" for row in result.json()["items"]))

    def test_company_search_api_uses_search_service(self):
        client=TestClient(app)
        with patch("backend.main.companies.search",new_callable=AsyncMock,return_value=[{"symbol":"ABC","exchange":"NS","name":"ABC Ltd"}]):
            result=client.get("/api/companies/search?q=ABC")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["items"][0]["exchange"],"NS")
        self.assertEqual(client.get("/api/companies/search?q=a").status_code,400)

    def test_market_chart_rejects_unsupported_period(self):
        client=TestClient(app)
        self.assertEqual(client.get("/api/market?range=10y").status_code,400)

    def test_company_quote_endpoint_uses_requested_exchange_and_history(self):
        client=TestClient(app)
        with patch("backend.main.market.quote",new_callable=AsyncMock,return_value={"price":123,"history":[{"date":"Nov 14","value":123}],"volume_history":[{"date":"Nov 14","value":1000}],"status":"live"}) as quote:
            result=client.get("/api/market/quote/ABC?exchange=BO&range=1y")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["history"][0]["value"],123)
        self.assertEqual(quote.await_args.args,("ABC","BO","1y"))

    def test_announcement_window_validation_rejects_invalid_custom_dates(self):
        client=TestClient(app)
        self.assertEqual(client.get("/api/announcements?window=custom&from_date=not-a-date&to_date=2026-11-15").status_code,400)

    def test_health_checks_the_sqlite_store(self):
        self.assertEqual(TestClient(app).get("/api/health").json()["database"],"ok")

    def test_reference_categories_classify(self):
        service=AnnouncementClassificationService()
        examples={"fund_raise":"QIP fund raise","insider_trading":"Promoter bought shares","earnings_call":"Earnings call transcript","investor_meetings":"Investor conference meeting","credit_rating":"Credit rating rationale","dividend":"Interim dividend record date","bonus_split":"Bonus issue","capex":"Capital expenditure for new facility","press_release":"Press release product launch","postal_ballot":"Postal ballot notice","agm_egm":"Annual general meeting notice","pledging":"Promoter pledge disclosure"}
        for expected,title in examples.items():
            with self.subTest(title=title): self.assertEqual(service.classify(title),expected)

    def test_financial_extraction_keeps_periods_and_units_unassigned(self):
        rows=FinancialDataExtractionService().extract("Revenue from operations 1,240.5 1,110.0\nProfit after tax 125 102")
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["metrics"],["Revenue"])
        self.assertEqual(rows[0]["values"],["1,240.5","1,110.0"])
        self.assertEqual(rows[0]["periods"],[])
        self.assertIn("verify period",rows[0]["status"].lower())
        self.assertEqual(rows[0]["comparisons"],{})

    def test_qoq_and_yoy_only_calculate_from_explicit_period_columns(self):
        rows=FinancialDataExtractionService().extract("Q2 FY27 Q1 FY27 Q2 FY26\nRevenue from operations 120 100 90")
        self.assertEqual(rows[0]["periods"],["Q2 FY27","Q1 FY27","Q2 FY26"])
        self.assertEqual(rows[0]["comparisons"],{"current_period":"Q2 FY27","qoq_pct":20.0,"yoy_pct":33.33})

    def test_result_filing_rows_keep_period_comparisons_and_page_citations(self):
        source="https://www.nseindia.com/test/result.pdf"
        service=AnnouncementService()
        result=service.extract_result_pages(source,["Q2 FY27 Q1 FY27 Q2 FY26\nRevenue from operations 120 100 90\nEBITDA 20 18 15\nProfit before tax 10 8 7\nProfit after tax 7 6 5"],"result.pdf")
        self.assertEqual(result["status"],"available")
        self.assertEqual(len(result["highlights"]),4)
        revenue=result["highlights"][0]
        self.assertEqual(revenue["metrics"],["Revenue"])
        self.assertEqual(revenue["comparisons"],{"current_period":"Q2 FY27","qoq_pct":20.0,"yoy_pct":33.33})
        self.assertEqual(revenue["citation"],"result.pdf, page 1")
        with connect() as db: db.execute("DELETE FROM announcement_highlights WHERE source=?",(source,)); db.commit()

    def test_research_dossier_has_all_eight_sections_and_marks_missing_evidence(self):
        dossier=DocumentRetrievalService().dossier("NO_DOCUMENTS_TEST")
        self.assertEqual(len(dossier["sections"]),8)
        self.assertTrue(all(s["status"]=="unavailable" for s in dossier["sections"]))
        self.assertEqual(dossier["financial_rows"],[])

    def test_ingested_primary_source_populates_cited_research_sections(self):
        symbol="FINN_RESEARCH_WORKFLOW_TEST"
        service=DocumentRetrievalService()
        result=service.ingest(symbol,"Test annual report","company IR annual report","Business overview: the company manufactures industrial products and serves domestic customers.\n\nRevenue from operations 1,240.5 1,110.0\nProfit after tax 125 102\n\nCash flow from operating activities was positive during the year and working capital remained stable.")
        self.assertEqual(result["status"],"indexed")
        dossier=service.dossier(symbol)
        self.assertEqual(dossier["source_count"],1)
        self.assertTrue(any(s["name"]=="Business snapshot" and s["status"]=="evidence found" for s in dossier["sections"]))
        self.assertTrue(any(row["citation"].startswith("Test annual report, section ") for row in dossier["financial_rows"]))
        self.assertTrue(service.search(symbol,"Revenue cash flow"))
        with connect() as db:
            ids=[row[0] for row in db.execute("SELECT id FROM documents WHERE symbol=?",(symbol,))]
            db.executemany("DELETE FROM chunks WHERE document_id=?",[(doc_id,) for doc_id in ids])
            db.execute("DELETE FROM documents WHERE symbol=?",(symbol,)); db.commit()

    def test_html_retrieval_uses_section_citations_and_bounded_chunks(self):
        service=DocumentRetrievalService()
        chunks=service._chunk_text("Business overview: " + ("company operations and segment revenue. "*100))
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(len(chunk)<=1400 for chunk in chunks))
        result=service.ingest("FINN_HTML_SECTION_TEST","IR page","https://company.com/investors", "Revenue from operations 120 100 for the reported quarter and prior comparable period")
        with connect() as db:
            row=db.execute("SELECT page,citation FROM chunks WHERE document_id=?",(result["document_id"],)).fetchone()
            db.execute("DELETE FROM chunks WHERE document_id=?",(result["document_id"],))
            db.execute("DELETE FROM documents WHERE id=?",(result["document_id"],)); db.commit()
        self.assertIsNone(row["page"])
        self.assertIn("section",row["citation"])

    def test_ai_provider_failure_degrades_to_source_excerpts(self):
        service=AIAnalysisService(); service.base="https://model.invalid/v1"; service.key="configured"; service.model="model"
        doc={"text":"Revenue was 120 in Q2 FY27.","citation":"Results, page 4","source":"https://www.nseindia.com/result.pdf"}
        with patch("backend.services.core.httpx.AsyncClient",side_effect=RuntimeError("offline")):
            answer=asyncio.run(service.ask("What was revenue?",[doc]))
        self.assertEqual(answer["status"],"provider_error")
        self.assertEqual(answer["citations"][0]["citation"],doc["citation"])

    def test_ai_not_configured_returns_primary_source_passages_and_explicit_status(self):
        service=AIAnalysisService(); service.base=""; service.key=""; service.model=""
        doc={"text":"Revenue was 120 in Q2 FY27.","citation":"Results, page 4","source":"https://www.nseindia.com/result.pdf"}
        answer=asyncio.run(service.ask("What was revenue?",[doc],symbol="RELIANCE"))
        self.assertEqual(answer["status"],"needs_configuration")
        self.assertEqual(answer["citations"][0]["citation"],doc["citation"])

    def test_ai_uses_company_and_followup_context_and_filters_citations(self):
        import json
        service=AIAnalysisService(); service.base="https://model.invalid/v1"; service.key="configured"; service.model="model"
        service.complete=AsyncMock(return_value=json.dumps({"answer":"Cash conversion weakened.","verified_facts":["Revenue was 120."],"interpretation":"Working capital may have contributed.","citations":[{"citation":"Invented, page 9","source":"https://example.invalid/fake.pdf"}],"confidence":"medium"}))
        doc={"text":"Revenue was 120 in Q2 FY27; operating cash flow was 70.","citation":"Annual report, page 24","source":"https://www.nseindia.com/report.pdf"}
        history=[{"role":"user","content":"How did profit change?"},{"role":"assistant","content":"The indexed source reports a change."}]
        answer=asyncio.run(service.ask("Did cash flow keep pace?",[doc],history,"HDFCBANK"))
        call=service.complete.await_args
        self.assertIn("Selected company ticker: HDFCBANK",call.args[0][-1]["content"])
        self.assertEqual(call.args[0][1]["content"],history[0]["content"])
        self.assertEqual(answer["status"],"answered")
        self.assertEqual(answer["citations"][0]["citation"],doc["citation"])

    def test_invalid_model_json_is_reported_as_unstructured_and_keeps_real_sources(self):
        service=AIAnalysisService(); service.base="https://model.invalid/v1"; service.key="configured"; service.model="model"
        service.complete=AsyncMock(return_value="not json; revenue is 999")
        doc={"text":"Revenue table is unavailable in this excerpt.","citation":"Filing, page 2","source":"https://www.nseindia.com/filing.pdf"}
        answer=asyncio.run(service.ask("What is revenue?",[doc],symbol="INFY"))
        self.assertEqual(answer["status"],"answered_unstructured")
        self.assertEqual(answer["citations"][0]["source"],doc["source"])

    def test_chat_api_validates_history_size(self):
        response=TestClient(app).post("/api/research/TESTXYZ/ask",json={"question":"What is revenue?","history":[{"role":"user","content":"previous question"}]*9})
        self.assertEqual(response.status_code,422)

    def test_research_without_sources_reports_gap(self):
        client=TestClient(app)
        result=client.post("/api/research/TESTXYZ/ask",json={"question":"What is revenue growth?"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["status"],"unavailable")
        self.assertEqual(result.json()["citations"],[])
        dossier=client.get("/api/research/TESTXYZ").json()
        self.assertEqual(len(dossier["dossier"]["sections"]),8)
        self.assertEqual(dossier["dossier"]["status"],"unavailable")

    def test_api_health_and_sample_feed(self):
        client=TestClient(app)
        self.assertEqual(client.get("/api/health").json()["status"],"ok")
        feed=client.get("/api/announcements?window=all")
        self.assertEqual(feed.status_code,200)
        self.assertIn(feed.json()["status"],{"sample","cached","live","partial"})
        if feed.json()["status"]=="sample": self.assertTrue(all(item["status"]=="sample" for item in feed.json()["items"]))

    def test_result_highlights_reject_non_exchange_sources(self):
        client=TestClient(app)
        response=client.post("/api/announcements/highlights",json={"source":"http://example.com/results.pdf"})
        self.assertEqual(response.status_code,400)

    def test_portfolio_add_and_remove(self):
        client=TestClient(app)
        added=client.post("/api/portfolio",json={"symbol":"FINNTEST","name":"Test Holding","sector":"Test","exchange":"BO"})
        self.assertEqual(added.status_code,200)
        self.assertEqual(added.json()["item"]["exchange"],"BO")
        removed=client.delete("/api/portfolio/FINNTEST")
        self.assertEqual(removed.status_code,200)
        self.assertEqual(client.delete("/api/portfolio/FINNTEST").status_code,404)

    def test_portfolio_rejects_unsupported_exchange(self):
        response=TestClient(app).post("/api/portfolio",json={"symbol":"BAD","name":"Bad exchange","exchange":"LSE"})
        self.assertEqual(response.status_code,422)

if __name__=="__main__": unittest.main()
