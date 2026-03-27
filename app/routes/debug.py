from fastapi import APIRouter

from app.scheduler import scheduler

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/jobs")
def list_scheduler_jobs():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {"job_count": len(jobs), "jobs": jobs}
