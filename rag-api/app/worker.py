"""In-process asyncio worker pool.

V1 ships the worker inside the same uvicorn process for ops simplicity.
If pilot load justifies it, this same module can be invoked as a standalone
process (`python -m app.worker`) — `start_worker_pool()` just blocks forever.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import _submit_meta, queue, pipeline, webhook
from .deps import get_config
from .models import ErrorBody, ErrorCode, Job, JobStatus, RFS

log = logging.getLogger("rag-api.worker")

_tasks: list[asyncio.Task] = []
_stop_event = asyncio.Event()


async def _process_one(job: Job) -> None:
    log.info('"job.start id=%s lodge=%s priority=%s"',
             job.job_id, job.rfs_lodge_id, job.priority.value)

    if queue.is_cancelled(str(job.job_id)):
        queue.update_job(str(job.job_id), status=JobStatus.cancelled.value,
                         completed_at=datetime.now(timezone.utc))
        queue.clear_cancel(str(job.job_id))
        return

    # Move queued → running
    job = queue.update_job(str(job.job_id), status=JobStatus.running.value)
    if job is None:
        return  # state expired

    try:
        meta = _submit_meta.load_submit_meta(str(job.job_id))
        rfs_dict = meta.get("rfs")
        if rfs_dict:
            rfs = RFS.model_validate(rfs_dict)
        else:
            log.warning('"job.rfs_missing_meta id=%s — running minimal RFS"',
                        job.job_id)
            rfs = RFS(lodge_id=job.rfs_lodge_id or "unknown",
                      notes="<rfs payload missing>")
        analysis, trace, usage = await pipeline.run_pipeline(job, rfs)

        if queue.is_cancelled(str(job.job_id)):
            queue.update_job(str(job.job_id), status=JobStatus.cancelled.value,
                             completed_at=datetime.now(timezone.utc),
                             agent_trace=[s.model_dump() for s in trace],
                             usage=usage.model_dump())
            queue.clear_cancel(str(job.job_id))
            return

        job = queue.update_job(
            str(job.job_id),
            status=JobStatus.succeeded.value,
            completed_at=datetime.now(timezone.utc),
            result=analysis.model_dump(),
            agent_trace=[s.model_dump() for s in trace],
            usage=usage.model_dump(),
        )
        await webhook.deliver(job)

    except Exception as e:
        log.exception('"job.failed id=%s err=%s"', job.job_id, e)
        job = queue.update_job(
            str(job.job_id),
            status=JobStatus.failed.value,
            completed_at=datetime.now(timezone.utc),
            error=ErrorBody(code=ErrorCode.internal, message=str(e)).model_dump(),
        )
        if job:
            await webhook.deliver(job)


async def _worker_loop(worker_id: int) -> None:
    log.info('"worker.up id=%d"', worker_id)
    cfg = get_config()
    while not _stop_event.is_set():
        try:
            job = await asyncio.to_thread(queue.dequeue, 1)
        except Exception as e:
            log.exception('"worker.dequeue_error id=%d err=%s"', worker_id, e)
            await asyncio.sleep(cfg.worker_poll_interval_seconds)
            continue
        if job is None:
            continue
        try:
            await _process_one(job)
        except Exception as e:
            log.exception('"worker.process_error id=%d job=%s err=%s"',
                          worker_id, job.job_id, e)


async def start_worker_pool() -> None:
    cfg = get_config()
    for i in range(cfg.worker_concurrency):
        _tasks.append(asyncio.create_task(_worker_loop(i)))
    log.info('"worker.pool_started concurrency=%d"', cfg.worker_concurrency)


async def stop_worker_pool() -> None:
    _stop_event.set()
    for t in _tasks:
        t.cancel()
    _tasks.clear()


if __name__ == "__main__":
    # Standalone-worker entry point (used once we split the process).
    async def _main():
        await start_worker_pool()
        await asyncio.Event().wait()
    asyncio.run(_main())
