from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="IPv6 双栈测绘 API",
    description="通过核心交换机 ARP / IPv6 邻居表统计双栈终端数量",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"message": "IPv6 Dual-Stack Survey API", "docs": "/docs"}
