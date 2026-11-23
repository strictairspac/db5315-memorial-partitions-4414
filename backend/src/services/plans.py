import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from src.config.settings import engine
from src.constants.workflow import PlanStatus
from src.models.tables import cases, evidence, plans
from src.services import domain
from src.services.workflow import found, log, recent
from src.utils.encoding import canonical
from src.utils.time import utc_time


class _Rejected(Exception):
    pass


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _plan_key(case_id, source_digest, baseline_digest, baseline_signature, attempt):
    return _digest({'case_id': case_id, 'source': source_digest,
                    'baseline': baseline_digest or '', 'signature': baseline_signature or '',
                    'attempt': attempt})


def _live_plan(conn, case_id, source_digest, baseline_digest, baseline_signature):
    """Reuse the plan that already represents this exact basis.

    Repeating a preview for unchanged source data must not mint another version:
    first prefer the latest applied plan (re-applying it is a no-op), then any plan
    with the same source and baseline — including one that went stale and whose basis
    has since returned. Rejected/failed plans are only reused once their basis holds again.
    """
    applied = _latest_applied(conn, case_id)
    if applied and applied['source_digest'] == source_digest:
        return applied
    statement = (select(plans).where(
        plans.c.case_id == case_id,
        plans.c.source_digest == source_digest,
        plans.c.status.in_([PlanStatus.PREVIEWED, PlanStatus.APPLYING, PlanStatus.STALE,
                            PlanStatus.FAILED]),
        plans.c.baseline_evidence_digest.is_not_distinct_from(baseline_digest),
        plans.c.baseline_evidence_signature.is_not_distinct_from(baseline_signature))
        .order_by(plans.c.id.desc()).limit(1))
    row = conn.execute(statement).mappings().first()
    return dict(row) if row else None


def _latest_applied(conn, case_id):
    row = conn.execute(select(plans).where(plans.c.case_id == case_id,
                        plans.c.status == PlanStatus.APPLIED)
                       .order_by(plans.c.id.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def _evidence_of(conn, plan_id):
    row = conn.execute(select(evidence).where(evidence.c.plan_id == plan_id)).mappings().first()
    return dict(row) if row else None


def _signature(proof):
    """Fingerprint of a committed evidence's signature state."""
    if not proof:
        return None
    return _digest({'digest': proof['digest'], 'reviewer': proof['reviewer'],
                    'reviewed_at': proof['reviewed_at']})


def _baseline_state(conn, plan):
    base_id = plan.get('baseline_plan_id')
    if not base_id:
        return None
    base = conn.execute(select(plans.c.report).where(plans.c.id == base_id)).scalar()
    return (base or {}).get('state')


def _mark_previews_stale(conn, case_id, reason, keep=None):
    rows = conn.execute(select(plans.c.id).where(plans.c.case_id == case_id,
                        plans.c.status == PlanStatus.PREVIEWED)).scalars().all()
    for identity in rows:
        if identity == keep:
            continue
        conn.execute(plans.update().where(plans.c.id == identity)
                     .values(status=PlanStatus.STALE, rejection_reason=reason[:500]))
        log(conn, 'system', 'stale_plan', identity)


def preview_plan(case_id, actor):
    """Record what applying the case's current cutoff plan would change.

    The plan key is deterministic over (case, source payload, baseline evidence),
    so repeating the same request returns the original plan instead of adding another.
    """
    with engine.begin() as conn:
        case = found(conn, cases, case_id)
        # Serialize preview creation per case so a double click cannot mint two versions.
        conn.execute(select(cases.c.id).where(cases.c.id == case_id).with_for_update())
        try:
            domain.validate(case['payload'])
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        source_digest = _digest(case['payload'])
        base = _latest_applied(conn, case_id)
        base_evidence = _evidence_of(conn, base['id']) if base else None
        baseline_digest = base_evidence['digest'] if base_evidence else None
        baseline_signature = _signature(base_evidence)
        baseline_state = (base['report'] or {}).get('state') if base else None
        existing = _live_plan(conn, case_id, source_digest, baseline_digest, baseline_signature)
        if existing:
            if existing['status'] == PlanStatus.STALE:
                # The basis is valid again, so the old preview applies again as-is.
                conn.execute(plans.update().where(plans.c.id == existing['id'])
                             .values(status=PlanStatus.PREVIEWED, rejection_reason=None))
                log(conn, actor, 'revive_plan', existing['id'])
                return found(conn, plans, existing['id'])
            return existing
        version = (conn.execute(select(plans.c.version).where(plans.c.case_id == case_id)
                                .order_by(plans.c.version.desc()).limit(1)).scalar() or 0) + 1
        preview = domain.preview(case['payload'], baseline_state)
        plan_id = conn.execute(plans.insert().values(
            case_id=case_id, version=version,
            plan_key=_plan_key(case_id, source_digest, baseline_digest, baseline_signature, version),
            cutoff=preview['cutoff'],
            payload=case['payload'], source_digest=source_digest,
            baseline_plan_id=base['id'] if base else None,
            baseline_evidence_id=base_evidence['id'] if base_evidence else None,
            baseline_evidence_digest=baseline_digest,
            baseline_evidence_signature=baseline_signature, status=PlanStatus.PREVIEWED,
            preview=preview, preview_digest=_digest(preview), author=actor,
            created_at=utc_time())).inserted_primary_key[0]
        _mark_previews_stale(conn, case_id, f'已有更新的方案 v{version}，原预览失效。', keep=plan_id)
        log(conn, actor, 'preview_plan', plan_id)
        return found(conn, plans, plan_id)


def update_case(case_id, title, payload, actor):
    """Revise source data. A pending preview becomes stale only if the data changed."""
    with engine.begin() as conn:
        case = found(conn, cases, case_id)
        try:
            domain.validate(payload)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        values = {'payload': payload}
        if title is not None:
            title = title.strip()
            if not title:
                raise HTTPException(422, '档案名称不能为空')
            values['title'] = title
        if (title is None or title == case['title']) and _digest(payload) == _digest(case['payload']):
            return case
        conn.execute(cases.update().where(cases.c.id == case_id).values(**values))
        log(conn, actor, 'update_case', case_id)
        if _digest(payload) != _digest(case['payload']):
            _mark_previews_stale(conn, case_id, '源数据已变更，原预览失效，请重新预览。')
        return found(conn, cases, case_id)


def _stale_reason(conn, plan, case):
    """Return why the preview no longer holds, or None if it is still valid."""
    if _digest(case['payload']) != plan['source_digest']:
        return '源数据已变更，原方案失效，请重新预览。'
    base = _latest_applied(conn, plan['case_id'])
    if base and base['id'] != plan['baseline_plan_id']:
        return f'上次证据已变化（当前以方案 v{base["version"]} 的已签证据为准），原方案失效。'
    if not base and plan['baseline_plan_id'] is not None:
        return '上次证据已变化，原方案失效。'
    if base:
        proof = _evidence_of(conn, base['id'])
        if not proof or proof['digest'] != plan['baseline_evidence_digest']:
            return '上次已签证据校验码已变化，原方案失效。'
        if _signature(proof) != plan['baseline_evidence_signature']:
            return '上次证据的签署状态已变化，原方案失效，请重新预览。'
    recomputed = domain.preview(case['payload'], (base['report'] or {}).get('state') if base else None)
    if _digest(recomputed) != plan['preview_digest']:
        return '预览所依据的数据已变化，原方案失效，请重新预览。'
    return None


def _result(conn, plan):
    return {'plan': plan, 'evidence': _evidence_of(conn, plan['id'])}


def apply_plan(plan_id, actor):
    """Mount, detach and reconcile in one transaction, then record signed evidence.

    Everything runs inside a single transaction, so any failure rolls back to the
    previously committed version; its plan and signed evidence stay untouched.
    Re-applying an already applied plan returns the original result and adds nothing.
    """
    try:
        with engine.begin() as conn:
            plan = conn.execute(select(plans).where(plans.c.id == plan_id)
                                .with_for_update()).mappings().first()
            if plan is None:
                raise HTTPException(404, '找不到该方案')
            plan = dict(plan)
            # Serialize applies per case so two plans cannot both apply off one baseline.
            conn.execute(select(cases.c.id).where(cases.c.id == plan['case_id']).with_for_update())
            if plan['status'] == PlanStatus.APPLIED:
                return _result(conn, plan)
            if plan['status'] not in (PlanStatus.PREVIEWED, PlanStatus.FAILED):
                raise _Rejected(plan['rejection_reason']
                                or f'方案 v{plan["version"]} 当前状态为「{plan["status"]}」，不能生效。')
            case = found(conn, cases, plan['case_id'])
            reason = _stale_reason(conn, plan, case)
            if reason:
                raise _Rejected(reason)
            baseline_state = _baseline_state(conn, plan)
            conn.execute(plans.update().where(plans.c.id == plan_id)
                         .values(status=PlanStatus.APPLYING, error=None, rejection_reason=None))
            report = domain.solve_in(conn, plan['payload'], baseline_state)
            report['plan'] = {
                'id': plan_id, 'version': plan['version'], 'cutoff': plan['cutoff'],
                'source_digest': plan['source_digest'], 'preview_digest': plan['preview_digest'],
                'baseline_version': conn.execute(select(plans.c.version).where(
                    plans.c.id == plan['baseline_plan_id'])).scalar() if plan['baseline_plan_id'] else None,
            }
            conn.execute(evidence.insert().values(run_id=None, plan_id=plan_id, report=report,
                                                  digest=_digest(report)))
            conn.execute(plans.update().where(plans.c.id == plan_id).values(
                status=PlanStatus.APPLIED, report=report, applied_at=utc_time()))
            _mark_previews_stale(conn, plan['case_id'],
                                 f'方案 v{plan["version"]} 已生效，原预览失效，请基于最新证据重新预览。',
                                 keep=plan_id)
            log(conn, actor, 'apply_plan', plan_id)
            return _result(conn, found(conn, plans, plan_id))
    except _Rejected as exc:
        _record_rejection(plan_id, str(exc))
        raise HTTPException(409, str(exc)) from None
    except HTTPException:
        raise
    except Exception as exc:
        _record_failure(plan_id, f'生效失败，已保留旧版状态：{exc}')
        raise HTTPException(500, f'生效失败，已保留旧版状态：{exc}') from None


def _record_rejection(plan_id, reason):
    with engine.begin() as conn:
        row = found(conn, plans, plan_id)
        if row['status'] == PlanStatus.APPLIED:
            return
        conn.execute(plans.update().where(plans.c.id == plan_id)
                     .values(status=PlanStatus.STALE, rejection_reason=reason[:500]))
        log(conn, 'system', 'stale_plan', plan_id)


def _record_failure(plan_id, message):
    with engine.begin() as conn:
        row = found(conn, plans, plan_id)
        if row['status'] == PlanStatus.APPLIED:
            return
        conn.execute(plans.update().where(plans.c.id == plan_id)
                     .values(status=PlanStatus.FAILED, error=message[:500]))
        log(conn, 'system', 'fail_plan', plan_id)


def list_plans():
    return recent(plans)


def plan_detail(identity):
    with engine.connect() as conn:
        plan = found(conn, plans, identity)
        return {**plan, 'evidence': _evidence_of(conn, identity)}


def export_plan(identity):
    with engine.connect() as conn:
        plan = found(conn, plans, identity)
        proof = _evidence_of(conn, identity)
    return {
        'schema': 'retention-plan/v1',
        'case_id': plan['case_id'], 'plan_id': plan['id'], 'version': plan['version'],
        'cutoff': plan['cutoff'], 'status': plan['status'], 'author': plan['author'],
        'created_at': plan['created_at'], 'applied_at': plan['applied_at'],
        'source_digest': plan['source_digest'], 'preview_digest': plan['preview_digest'],
        'baseline_plan_id': plan['baseline_plan_id'],
        'baseline_evidence_id': plan['baseline_evidence_id'],
        'baseline_evidence_digest': plan['baseline_evidence_digest'],
        'baseline_evidence_signature': plan['baseline_evidence_signature'],
        'rejection_reason': plan['rejection_reason'], 'error': plan['error'],
        'preview': plan['preview'], 'report': plan['report'],
        'evidence': ({'id': proof['id'], 'digest': proof['digest'], 'reviewer': proof['reviewer'],
                      'review_note': proof['review_note'], 'reviewed_at': proof['reviewed_at']}
                     if proof else None),
    }
