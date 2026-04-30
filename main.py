from fastapi import FastAPI
from app.scraper.scanner import run_lepe_scan

app = FastAPI(title="Lepefy Backend API")

@app.get("/")
def read_root():
    return {"message": "Welcome to Lepefy API - Connection Active"}

# Nuovo comando per testare lo scanner
@app.get("/test-scan")
async def test_scan(q: str = "Nikon Z6"):
    try:
        data = await run_lepe_scan(q)
        return {
            "status": "success",
            "keyword": q,
            "found_items": data
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
