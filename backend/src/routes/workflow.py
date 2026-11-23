from fastapi import APIRouter, Depends
from sqlalchemy import text

from src.config.settings import cache, engine
from src.controllers.workflow import download, download_plan
from src.models.tables import audit, cases, evidence, runs
from src.schemas.workflow import CaseForm, CaseUpdateForm, LoginForm, ReviewForm
from src.services import domain, plans, workflow
from src.services.security import current_user, login, operator, reviewer

router = APIRouter()


@router.get('/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1')).scalar_one()
    cache.ping()
    return {'ok': True}


@router.post('/login')
def sign_in(form: LoginForm):
    return login(form.username, form.password)


@router.get('/spec')
def spec(_=Depends(current_user)):
    return domain.SPEC


@router.get('/cases')
def list_cases(_=Depends(current_user)):
    return workflow.recent(cases)


@router.post('/cases', status_code=201)
def create_case(form: CaseForm, actor=Depends(operator)):
    return workflow.make_case(form.title, form.payload, actor)


@router.put('/cases/{case_id}')
def revise_case(case_id: int, form: CaseUpdateForm, actor=Depends(operator)):
    return plans.update_case(case_id, form.title, form.payload, actor)


@router.get('/runs')
def list_runs(_=Depends(current_user)):
    return workflow.recent(runs)


@router.post('/cases/{case_id}/runs', status_code=202)
def start_run(case_id: int, actor=Depends(operator)):
    return workflow.enqueue(case_id, actor)


@router.get('/plans')
def list_plans(_=Depends(current_user)):
    return plans.list_plans()


@router.post('/cases/{case_id}/plans', status_code=201)
def preview_case_plan(case_id: int, actor=Depends(operator)):
    return plans.preview_plan(case_id, actor)


@router.get('/plans/{identity}')
def plan_detail(identity: int, _=Depends(current_user)):
    return plans.plan_detail(identity)


@router.post('/plans/{identity}/apply')
def apply_case_plan(identity: int, actor=Depends(operator)):
    return plans.apply_plan(identity, actor)


@router.get('/plans/{identity}/download')
def export_plan(identity: int, _=Depends(current_user)):
    return download_plan(identity)


@router.get('/evidence')
def list_evidence(_=Depends(current_user)):
    return workflow.recent(evidence)


@router.get('/evidence/{identity}/download')
def export_evidence(identity: int, _=Depends(current_user)):
    return download(identity)


@router.post('/evidence/{identity}/review')
def review_evidence(identity: int, form: ReviewForm, actor=Depends(reviewer)):
    return workflow.review(identity, form.note, actor)


@router.get('/audit')
def list_audit(_=Depends(current_user)):
    return workflow.recent(audit)
