import json

import redis
from fastapi import APIRouter, Depends, HTTPException, Response

from src.config.database import db
from src.controllers.business import experiment
from src.middlewares.auth import admin, current, editor
from src.models.entities import DpRelease, Experiment, PrivacyBudget
from src.schemas.privacy import PrivacyBudgetInput, PrivacyClaimInput
from src.services.business import audit
from src.constants.privacy import DEFAULT_EPSILON_PER_RELEASE, DEFAULT_TOTAL_EPSILON
from src.services.privacy import (
    BudgetExhausted, EmptySample, budget_for, budget_public, claim, configured_budget,
    external, now, public, q6, retry,
)

router = APIRouter()
STATUSES = ('queued', 'running', 'done', 'failed')


def _release(s, i):
    row = s.get(DpRelease, i)
    if not row:
        raise HTTPException(404, '发布任务不存在')
    return row


@router.get('/privacy/budgets')
def budgets(u=Depends(current), s=Depends(db)):
    """只读展示；未配置的实验显示预设默认值，不因 GET 落库（观察员只读）。"""
    result = []
    for exp in s.query(Experiment).order_by(Experiment.id).all():
        row = s.query(PrivacyBudget).filter_by(experiment_id=exp.id).first()
        if row:
            result.append({'code': exp.code, 'name': exp.name, **budget_public(s, row)})
        else:
            total, per = q6(DEFAULT_TOTAL_EPSILON), q6(DEFAULT_EPSILON_PER_RELEASE)
            result.append({
                'code': exp.code, 'name': exp.name, 'experiment_id': exp.id,
                'total_epsilon': float(total), 'used_epsilon': 0.0,
                'remaining_epsilon': float(total), 'epsilon_per_release': float(per),
                'releases_charged': 0,
            })
    return result


@router.put('/privacy/budgets/{experiment_id}')
def configure_budget(experiment_id: int, data: PrivacyBudgetInput, u=Depends(admin), s=Depends(db)):
    exp = experiment(s, experiment_id)
    row = budget_for(s, exp.id)  # 配置写入时才建行（首次可显式落库）
    try:
        total, per = configured_budget(row, data.total_epsilon, data.epsilon_per_release)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if per <= 0 or total <= 0:
        raise HTTPException(422, '预算精度为 0.000001，请提高额度')
    row.total_epsilon = total
    row.epsilon_per_release = per
    row.updated_at = now()
    s.commit()
    audit(s, u, f'配置隐私预算 实验{exp.id} 总{total} 单次{per}')
    return {'code': exp.code, 'name': exp.name, **budget_public(s, row)}


@router.get('/privacy/releases')
def releases(experiment_id: int | None = None, status: str | None = None, u=Depends(current), s=Depends(db)):
    q = s.query(DpRelease)
    if experiment_id:
        q = q.filter_by(experiment_id=experiment_id)
    if status in STATUSES:
        q = q.filter_by(status=status)
    return [public(r) for r in q.order_by(DpRelease.id.desc()).limit(100)]


@router.post('/privacy/releases')
def request_release(data: PrivacyClaimInput, u=Depends(editor), s=Depends(db)):
    exp = experiment(s, data.experiment_id)
    try:
        row, reused = claim(s, redis, exp, u)
    except (BudgetExhausted, EmptySample) as exc:
        raise HTTPException(409, str(exc))
    audit(s, u, f'领取差分汇总 实验{exp.id} 样本{row.sample_ref} '
                + ('复用既有结果' if reused else f'扣减ε={row.epsilon}'))
    return {**public(row), 'reused': reused}


@router.get('/privacy/releases/{i}')
def release_detail(i: int, u=Depends(current), s=Depends(db)):
    row = _release(s, i)
    exp = experiment(s, row.experiment_id)
    return {**public(row), 'experiment': {'id': exp.id, 'code': exp.code, 'name': exp.name}}


@router.post('/privacy/releases/{i}/retry')
def release_retry(i: int, u=Depends(editor), s=Depends(db)):
    row = _release(s, i)
    if row.status == 'done':
        raise HTTPException(409, '任务已完成，结果固定；重试不会换取新噪声')
    if row.status == 'running':
        raise HTTPException(409, '任务正在计算，请等待完成或失败后再重试')
    # queued 也允许重新入队：用于扣费后中断、队列消息丢失的恢复；重复入队幂等，不重复扣费。
    try:
        retry(s, redis, row)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    audit(s, u, f'重试差分汇总任务 {i}（不重复扣费、不更换噪声）')
    return public(row)


@router.get('/privacy/releases/{i}/export')
def release_export(i: int, u=Depends(current), s=Depends(db)):
    row = _release(s, i)
    exp = experiment(s, row.experiment_id)
    try:
        payload = external(row, exp)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    audit(s, u, f'导出差分汇总 任务{i} 样本{row.sample_ref}')
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="dp-channel-{row.sample_ref}.json"'},
    )


@router.post('/privacy/releases/{i}/deliver')
def release_deliver(i: int, u=Depends(editor), s=Depends(db)):
    """外发是同一冻结结果的重放：返回同份内容，不再扣费、不再产生新噪声。"""
    row = _release(s, i)
    exp = experiment(s, row.experiment_id)
    try:
        payload = external(row, exp)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    repeated = bool(row.delivered_at)
    if not repeated:
        row.delivered_by = u.email
        row.delivered_at = now()
        s.commit()
        audit(s, u, f'外发差分汇总 任务{i} 样本{row.sample_ref} ε={row.epsilon}')
    return {
        **payload,
        'delivery': {
            'by': row.delivered_by or u.email,
            'at': row.delivered_at or now(),
            'repeated': repeated,
        },
    }
