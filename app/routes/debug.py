from fastapi import APIRouter, Depends, Request

from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.scheduler import scheduler

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/jobs", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def list_scheduler_jobs(request: Request):
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {"job_count": len(jobs), "jobs": jobs}
