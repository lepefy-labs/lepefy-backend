from fastapi import FastAPI

app = FastAPI(title="Lepefy Backend API")

@app.get("/")
def read_root():
    return {"message": "Welcome to Lepefy API - Spot the Value."}

@app.get("/health")
def health_check():
    return {"status": "online", "region": "Dschang/Lepe Connection Active"}
