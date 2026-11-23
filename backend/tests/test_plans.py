import copy

from conftest import login

from src.services.domain import SPEC

SAMPLE = copy.deepcopy(SPEC['sample'])


def _auth(env):
    client = env['client']
    return client, login(client, 'operator'), login(client, 'reviewer'), login(client, 'viewer')


def _create_case(client, operator, payload=None, title='测试档案'):
    response = client.post('/cases', headers=operator, json={'title': title, 'payload': payload or SAMPLE})
    assert response.status_code == 201, response.text
    return response.json()


def _preview(client, operator, case_id):
    response = client.post(f'/cases/{case_id}/plans', headers=operator)
    assert response.status_code == 201, response.text
    return response.json()


def _apply(client, operator, plan_id):
    return client.post(f'/plans/{plan_id}/apply', headers=operator)


def test_preview_lists_impact_without_applying(app_env):
    client, operator, _, _ = _auth(app_env)
    case = _create_case(client, operator)
    plan = _preview(client, operator, case['id'])
    assert plan['status'] == 'previewed'
    assert plan['preview']['summary']['摘除凭条'] == 2
    assert plan['preview']['summary']['在线凭条'] == 4
    sent = {row['凭条编号']: row for row in plan['preview']['slips']}
    assert sent['SM-0801-01']['新位置'] == '摘除表'
    assert sent['SM-0901-01']['新位置'] == '在线账簿'
    # preview alone creates no evidence and no applied plan state
    assert client.get('/evidence', headers=operator).json() == []
    assert plan['applied_at'] is None


def test_repeat_preview_and_apply_keep_single_result(app_env):
    client, operator, _, _ = _auth(app_env)
    case = _create_case(client, operator)
    first = _preview(client, operator, case['id'])
    second = _preview(client, operator, case['id'])
    assert first['id'] == second['id']
    assert len(client.get('/plans', headers=operator).json()) == 1

    applied = _apply(client, operator, first['id'])
    assert applied.status_code == 200
    evidence_id = applied.json()['evidence']['id']
    again = _apply(client, operator, first['id'])
    assert again.json()['evidence']['id'] == evidence_id
    assert len(client.get('/evidence', headers=operator).json()) == 1


def test_source_change_invalidates_preview_and_rejects_apply(app_env):
    client, operator, _, _ = _auth(app_env)
    case = _create_case(client, operator)
    plan = _preview(client, operator, case['id'])

    changed = copy.deepcopy(SAMPLE)
    changed['cutoff'] = '2026-10-01'
    assert client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': changed}).status_code == 200

    stale = client.get(f'/plans/{plan["id"]}', headers=operator).json()
    assert stale['status'] == 'stale'
    assert stale['rejection_reason']

    refused = _apply(client, operator, plan['id'])
    assert refused.status_code == 409
    assert client.get('/evidence', headers=operator).json() == []


def test_noop_save_keeps_preview_live(app_env):
    client, operator, _, _ = _auth(app_env)
    case = _create_case(client, operator)
    plan = _preview(client, operator, case['id'])
    saved = client.put(f'/cases/{case["id"]}', headers=operator,
                       json={'title': case['title'], 'payload': SAMPLE})
    assert saved.status_code == 200
    assert client.get(f'/plans/{plan["id"]}', headers=operator).json()['status'] == 'previewed'


def test_signature_change_invalidates_preview(app_env):
    client, operator, reviewer, _ = _auth(app_env)
    case = _create_case(client, operator)
    first = _preview(client, operator, case['id'])
    applied = _apply(client, operator, first['id']).json()
    assert client.post(f'/evidence/{applied["evidence"]["id"]}/review', headers=reviewer,
                       json={'note': '核对无误'}).status_code == 200

    changed = copy.deepcopy(SAMPLE)
    changed['cutoff'] = '2026-10-01'
    client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': changed})
    second = _preview(client, operator, case['id'])
    assert second['baseline_evidence_signature']

    # Re-signing is refused, but a tampered signature still invalidates the plan.
    with app_env['engine'].begin() as conn:
        from sqlalchemy import text
        conn.execute(text("UPDATE business_evidence SET reviewer='other' WHERE id=:i"),
                     {'i': applied['evidence']['id']})
    refused = _apply(client, operator, second['id'])
    assert refused.status_code == 409
    assert '签署状态' in refused.json()['detail']


def test_failure_preserves_previous_version(app_env, monkeypatch):
    client, operator, reviewer, _ = _auth(app_env)
    case = _create_case(client, operator)
    first = _preview(client, operator, case['id'])
    applied_first = client.post(f'/plans/{first["id"]}/apply', headers=operator).json()
    client.post(f'/evidence/{applied_first["evidence"]["id"]}/review', headers=reviewer,
                json={'note': '确认'})

    changed = copy.deepcopy(SAMPLE)
    changed['cutoff'] = '2026-10-01'
    client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': changed})
    second = _preview(client, operator, case['id'])

    from src.services import domain
    original = domain.solve_in

    def explode(conn, payload, baseline=None):
        original(conn, payload, baseline)
        raise RuntimeError('模拟对账失败')

    monkeypatch.setattr(domain, 'solve_in', explode)
    failed = _apply(client, operator, second['id'])
    assert failed.status_code == 500
    monkeypatch.setattr(domain, 'solve_in', original)

    still_applied = client.get(f'/plans/{first["id"]}', headers=operator).json()
    assert still_applied['status'] == 'applied'
    assert still_applied['evidence']['review_note'] == '确认'
    broken = client.get(f'/plans/{second["id"]}', headers=operator).json()
    assert broken['status'] == 'failed'

    retried = client.post(f'/plans/{second["id"]}/apply', headers=operator)
    assert retried.status_code == 200
    assert len(client.get('/evidence', headers=operator).json()) == 2


def test_signed_evidence_cannot_be_rewritten(app_env):
    client, operator, reviewer, viewer = _auth(app_env)
    case = _create_case(client, operator)
    plan = _preview(client, operator, case['id'])
    evidence = _apply(client, operator, plan['id']).json()['evidence']
    first = client.post(f'/evidence/{evidence["id"]}/review', headers=reviewer, json={'note': '第一次'})
    assert first.status_code == 200
    second = client.post(f'/evidence/{evidence["id"]}/review', headers=reviewer, json={'note': '改写'})
    assert second.status_code == 409
    detail = client.get(f'/plans/{plan["id"]}', headers=operator).json()
    assert detail['evidence']['review_note'] == '第一次'
    assert client.post(f'/plans/{plan["id"]}/apply', headers=viewer).status_code == 403


def test_versions_track_each_slips_old_and_new_destination(app_env):
    client, operator, reviewer, _ = _auth(app_env)
    case = _create_case(client, operator)

    def apply_cutoff(cutoff):
        payload = copy.deepcopy(SAMPLE)
        payload['cutoff'] = cutoff
        client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': payload})
        plan = _preview(client, operator, case['id'])
        result = _apply(client, operator, plan['id']).json()
        client.post(f'/evidence/{result["evidence"]["id"]}/review', headers=reviewer,
                    json={'note': cutoff})
        return result['evidence']

    first = apply_cutoff('2026-09-01')
    second = apply_cutoff('2026-08-01')
    assert first['report']['plan']['version'] == 1
    assert second['report']['plan']['baseline_version'] == 1
    rows = {row['凭条编号']: row for row in second['report']['sections'][2]['rows']}
    assert rows['SM-0801-01']['原位置'] == '摘除表'
    assert rows['SM-0801-01']['新位置'] == '在线账簿'
    assert rows['SM-0801-01']['变化'] == '是'
    assert second['report']['metrics']['摘除后回到在线'] == 2
    assert len(client.get('/plans', headers=operator).json()) == 2
    assert client.get(f'/evidence/{first["id"]}/download', headers=operator).status_code == 200


def test_export_carries_version_rejection_and_destinations(app_env):
    client, operator, _, viewer = _auth(app_env)
    case = _create_case(client, operator)
    plan = _preview(client, operator, case['id'])
    export = client.get(f'/plans/{plan["id"]}/download', headers=viewer)
    assert export.status_code == 200
    body = export.json()
    assert body['schema'] == 'retention-plan/v1'
    assert body['version'] == plan['version']
    assert body['preview']['slips']
    assert 'rejection_reason' in body


def test_new_preview_supersedes_older_preview_on_same_baseline(app_env):
    client, operator, _, _ = _auth(app_env)
    case = _create_case(client, operator)

    widened = copy.deepcopy(SAMPLE)
    widened['cutoff'] = '2026-10-01'
    client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': widened})
    first = _preview(client, operator, case['id'])

    narrowed = copy.deepcopy(SAMPLE)
    narrowed['cutoff'] = '2026-08-01'
    client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': narrowed})
    second = _preview(client, operator, case['id'])
    assert second['version'] == first['version'] + 1
    assert client.get(f'/plans/{first["id"]}', headers=operator).json()['status'] == 'stale'

    # Restoring the earlier payload revives its plan instead of minting a third version.
    client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': widened})
    revived = _preview(client, operator, case['id'])
    assert revived['id'] == first['id']
    assert len(client.get('/plans', headers=operator).json()) == 2

    assert _apply(client, operator, first['id']).status_code == 200
    refused = _apply(client, operator, second['id'])
    assert refused.status_code == 409
    assert '失效' in refused.json()['detail']


def test_invalid_source_is_reported_before_preview(app_env):
    client, operator, _, _ = _auth(app_env)
    broken = copy.deepcopy(SAMPLE)
    broken['cutoff'] = '2026-13-01'
    created = client.post('/cases', headers=operator, json={'title': '坏数据', 'payload': broken})
    assert created.status_code == 422
    case = _create_case(client, operator)
    bad_update = client.put(f'/cases/{case["id"]}', headers=operator, json={'payload': broken})
    assert bad_update.status_code == 422
    # the case keeps its previous payload
    assert client.get('/cases', headers=operator).json()[0]['payload'] == SAMPLE
