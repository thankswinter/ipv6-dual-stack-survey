import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.cli.readonly import list_allowed_commands
from app.cli.templates import DEVICE_CATALOG
from app.collectors.base import create_collector
from app.core.job_manager import job_manager
from app.core.models import (
    DeviceModelInfo,
    DevicePageResponse,
    DeviceRole,
    HealthResponse,
    JobStatus,
    StackType,
    SurveyJobCreateRequest,
    SurveyJobCreateResponse,
    SurveyJobSnapshot,
    SurveyRequest,
    SurveyResult,
    Vendor,
    VendorModelsResponse,
)
from app.core.scale import DEVICE_PAGE_SIZE_DEFAULT, TARGET_ARP_ENTRIES

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version="1.1.0")


@router.get("/vendors")
def list_vendors() -> dict:
    return {
        "vendors": [
            {"id": Vendor.HUAWEI.value, "name": "华为 (Huawei)"},
            {"id": Vendor.H3C.value, "name": "新华三 (H3C)"},
        ]
    }


@router.get("/vendors/{vendor}/models", response_model=VendorModelsResponse)
def list_models(vendor: Vendor) -> VendorModelsResponse:
    catalog = DEVICE_CATALOG.get(vendor, [])
    models = [
        DeviceModelInfo(vendor=vendor, model=item["model"], description=item["description"])
        for item in catalog
    ]
    return VendorModelsResponse(vendor=vendor, models=models)


@router.get("/readonly-commands")
def get_readonly_commands() -> dict:
    return {
        "policy": "read_only",
        "description": "仅允许 display 查询与会话级 screen-length，不会修改交换机配置",
        "commands": list_allowed_commands(),
    }


@router.post("/survey/jobs", response_model=SurveyJobCreateResponse)
def create_survey_job(body: SurveyJobCreateRequest) -> SurveyJobCreateResponse:
    request = SurveyRequest(**body.model_dump(exclude={"resume_job_id"}))
    try:
        job = job_manager.create_job(request, resume_job_id=body.resume_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SurveyJobCreateResponse(
        job_id=job.job_id,
        status=job.status,
        resumed_from=job.resumed_from,
    )


@router.get("/survey/jobs/{job_id}", response_model=SurveyJobSnapshot)
def get_survey_job(job_id: str) -> SurveyJobSnapshot:
    try:
        return job_manager.get_job(job_id).snapshot()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/survey/jobs/{job_id}/stream")
async def stream_survey_job(job_id: str) -> StreamingResponse:
    try:
        job_manager.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def event_generator():
        terminal = {JobStatus.COMPLETED, JobStatus.PAUSED, JobStatus.FAILED}
        while True:
            snapshot = job_manager.get_job(job_id).snapshot()
            payload = snapshot.model_dump(mode="json")
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if snapshot.status in terminal:
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/survey/jobs/{job_id}/devices", response_model=DevicePageResponse)
def get_job_devices(
    job_id: str,
    page: int = 1,
    page_size: int = DEVICE_PAGE_SIZE_DEFAULT,
    stack_type: StackType | None = None,
    role: DeviceRole | None = None,
) -> DevicePageResponse:
    try:
        devices, total, total_pages = job_manager.get_devices_page(
            job_id, page=page, page_size=page_size, stack_type=stack_type, role=role
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return DevicePageResponse(
        job_id=job_id,
        page=page,
        page_size=min(max(page_size, 1), 500),
        total=total,
        total_pages=total_pages,
        stack_type=stack_type.value if stack_type else None,
        devices=devices,
    )


@router.post("/survey/jobs/{job_id}/cancel", response_model=SurveyJobSnapshot)
def cancel_survey_job(job_id: str) -> SurveyJobSnapshot:
    try:
        return job_manager.cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/survey/jobs/{job_id}/resume", response_model=SurveyJobCreateResponse)
def resume_survey_job(job_id: str) -> SurveyJobCreateResponse:
    try:
        job = job_manager.resume_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SurveyJobCreateResponse(
        job_id=job.job_id,
        status=job.status,
        resumed_from=job.resumed_from,
    )


@router.post("/survey", response_model=SurveyResult)
def run_survey(request: SurveyRequest) -> SurveyResult:
    """同步采集（兼容旧接口）。"""
    collector = create_collector(
        vendor=request.vendor,
        model=request.model,
        host=request.host,
        username=request.username,
        password=request.password,
        port=request.port,
        timeout=request.timeout,
    )

    try:
        stats, records, warnings, raw_arp, raw_ipv6 = collector.survey()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"交换机连接或采集失败: {exc}",
        ) from exc

    return SurveyResult(
        vendor=request.vendor,
        model=request.model,
        host=request.host,
        statistics=stats,
        devices=records,
        raw_arp_entries=raw_arp,
        raw_ipv6_entries=raw_ipv6,
        unique_device_count=stats.host_count,
        warnings=warnings,
    )
