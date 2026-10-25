import hashlib
import uuid

from celery import shared_task
from .models import AgentRun
from . import runtime


@shared_task(name='agentapi.launch')
def launch(run_id, restart=False):
    run = AgentRun.objects.get(pk=run_id)
    if run.status not in ('starting', 'restarting'):
        return
    try:
        if restart:
            runtime.stop_run(run)
            run.generation += 1
        else:
            if run.project:
                runtime.stop_run(run)
            run.generation = 1
        flag = 'flag-{%s}' % uuid.uuid4().hex
        run.flag_sha256 = hashlib.sha256(flag.encode()).hexdigest()
        run.project = 'vfa-%s-%s' % (run.pk, run.generation)
        run.entrypoints = '{}'
        run.error = ''
        accepted = AgentRun.objects.filter(pk=run_id,
            status__in=('starting', 'restarting')).update(
                generation=run.generation, flag_sha256=run.flag_sha256,
                project=run.project, entrypoints='{}', error='')
        if not accepted:
            return
        project, endpoints = runtime.start_run(run, flag)
        import json
        updated = AgentRun.objects.filter(pk=run_id,
            status__in=('starting', 'restarting')).update(
                project=project, entrypoints=json.dumps(endpoints), status='running')
        if not updated:
            # A timeout or cancellation may request cleanup while an image is
            # being imported. Never resurrect that run as "running".
            runtime.stop_run(run)
            return
    except Exception as exc:
        try:
            runtime.stop_run(run)
            runtime.release_images(run)
        except Exception as cleanup_error:
            run.error = '%s；清理失败：%s' % (exc, cleanup_error)
        else:
            run.error = str(exc)
        AgentRun.objects.filter(pk=run_id,
            status__in=('starting', 'restarting')).update(
                status='failed', error=run.error)


@shared_task(name='agentapi.halt')
def halt(run_id):
    run = AgentRun.objects.get(pk=run_id)
    try:
        runtime.stop_run(run)
        run.status = 'stopped'
        run.error = ''
        run.entrypoints = '{}'
        run.save()
        try:
            runtime.release_images(run)
        except Exception:
            pass  # The target is stopped; image reclamation can be retried.
    except Exception as exc:
        run.status = 'failed'
        run.error = '停止失败：' + str(exc)
        run.save()
