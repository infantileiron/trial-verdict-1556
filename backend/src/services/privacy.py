"""差分隐私汇总发布：参与者聚合、贡献限定、预算扣减与确定性加噪。

口径（纯 ε-DP，顺序组合）：
- 冻结样本只发布 4 个去重人数：A/B 组各的曝光人数与激活人数。
- 事件唯一键保证每名参与者每种事件最多 1 条，且一人只能属于一组，
  因此每个计数的全局敏感度为 1。
- 单次领取额度 ε_r 按 4 个统计量平分，各加 Laplace(0, 4/ε_r) 噪声；
  一次发布的总支出恰为 ε_r（四次 ε_r/4 顺序组合）。
- 加噪后四舍五入、负值截断为 0、激活人数不超过曝光人数，均为后处理，
  不额外消耗预算，也不增加隐私风险。
- 噪声由持久化的 seed 与统计量序号确定性派生，重试/恢复不会换噪声。
"""
import hashlib
import hmac
import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from src.constants.privacy import (
    CONFIDENCE, DEFAULT_EPSILON_PER_RELEASE, DEFAULT_TOTAL_EPSILON,
    MECHANISM, NOTICE, SCHEMA, STATISTIC_COUNT, VERSION,
)
from src.models.entities import DpRelease, Event, PrivacyBudget

SIX = Decimal('0.000001')
STAT_NAMES = ('exposed_a', 'exposed_b', 'activated_a', 'activated_b')
LAPLACE_95 = math.log(20.0)  # Laplace(0,b) 双侧95%区间半宽 = b·ln20


class BudgetExhausted(Exception):
    """预算不足，拒绝新的外发领取。"""


class EmptySample(Exception):
    """实验尚无事件，没有可汇总的冻结样本；不扣预算。"""


def _key():
    return (os.environ.get('DP_FINGERPRINT_KEY') or os.environ['JWT_SECRET']).encode()


def q6(value):
    return Decimal(value).quantize(SIX, rounding=ROUND_HALF_UP)


def now():
    return datetime.now(timezone.utc).isoformat()


def budget_for(s, experiment_id):
    """取实验预算；不存在时按默认总预算/单次额度创建（seed 之外的兜底）。"""
    row = s.query(PrivacyBudget).filter_by(experiment_id=experiment_id).first()
    if row:
        return row
    row = PrivacyBudget(
        experiment_id=experiment_id,
        total_epsilon=q6(DEFAULT_TOTAL_EPSILON),
        epsilon_per_release=q6(DEFAULT_EPSILON_PER_RELEASE),
        used_epsilon=Decimal('0.000000'),
        updated_at=now(),
    )
    s.add(row)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        row = s.query(PrivacyBudget).filter_by(experiment_id=experiment_id).first()
    return row


def budget_public(s, row):
    total, used, per = Decimal(row.total_epsilon), Decimal(row.used_epsilon), Decimal(row.epsilon_per_release)
    return {
        'experiment_id': row.experiment_id,
        'total_epsilon': float(total),
        'used_epsilon': float(used),
        'remaining_epsilon': float(max(Decimal('0'), total - used)),
        'epsilon_per_release': float(per),
        'releases_charged': s.query(DpRelease).filter(DpRelease.experiment_id == row.experiment_id).count(),
    }


def configured_budget(budget, total, per):
    """校验并返回待写入的预算配置；不提交事务。"""
    total, per, used = q6(total), q6(per), Decimal(budget.used_epsilon)
    if total <= 0 or per <= 0:
        raise ValueError('总预算与单次额度必须大于 0')
    if per > total:
        raise ValueError('单次额度不能超过总预算')
    if total < used:
        raise ValueError('总预算不能低于已用额度')
    return total, per


def _canonical(experiment_id, events):
    payload = [[e.id, e.user_key, e.group, e.kind, e.occurred_at] for e in events]
    body = json.dumps([experiment_id, payload], ensure_ascii=False, separators=(',', ':'))
    return hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()


def aggregate(s, exp):
    """按参与者聚合曝光与激活；返回原始计数与冻结样本指纹。"""
    events = s.query(Event).filter_by(experiment_id=exp.id).order_by(Event.id).all()
    exposed = {g: set() for g in ('A', 'B')}
    activated = {g: set() for g in ('A', 'B')}
    for e in events:
        if e.group not in exposed:
            continue
        if e.kind == 'exposure':
            exposed[e.group].add(e.user_key)
        elif e.kind == 'activation':
            activated[e.group].add(e.user_key)
    # 贡献限定：每名参与者每组最多贡献 1 次曝光、1 次激活，激活必须已曝光。
    activated = {g: activated[g] & exposed[g] for g in exposed}
    counts = {
        'exposed_a': len(exposed['A']), 'exposed_b': len(exposed['B']),
        'activated_a': len(activated['A']), 'activated_b': len(activated['B']),
    }
    fingerprint = _canonical(exp.id, events)
    return counts, fingerprint, len(events)


def derive_seed(fingerprint, epsilon):
    """领取时一次性派生并持久化噪声种子；之后重算只依赖库内种子。"""
    body = f'{VERSION}|{fingerprint}|{epsilon:.6f}'.encode()
    return hmac.new(_key(), body, hashlib.sha256).hexdigest()


def laplace_sample(seed, index, scale):
    """确定性逆变换采样：u 由种子 HMAC 派生，x=b·sign(u−.5)·ln(1−2|u−.5|)。"""
    digest = hmac.new(bytes.fromhex(seed), str(index).encode(), hashlib.sha256).digest()
    u = int.from_bytes(digest[:8], 'big') / float(1 << 64)
    u = min(max(u, 1e-12), 1 - 1e-12)
    return -scale * math.copysign(1.0, u - 0.5) * math.log(1 - 2 * abs(u - 0.5))


def _availability(counts, half_width, clamped_counts=0, clamped_activation=False):
    reasons, notes = [], []
    minimum = min(counts['exposed_a'], counts['exposed_b'])
    if minimum < 20:
        reasons.append('至少一组的加噪曝光人数不足 20 人，渠道汇总不宜作为承诺依据')
    if minimum and half_width > minimum / 2:
        reasons.append('噪声误差半宽超过较小分组曝光人数的一半，只能看量级')
    if clamped_counts:
        notes.append('存在加噪后为负并截断为 0 的计数，说明该计数小于噪声尺度，只能看量级')
    if clamped_activation:
        notes.append('已按后处理约束把激活人数限制在曝光人数以内')
    return {
        'usable': not reasons,
        'reasons': reasons,
        'notes': notes,
        'hint': '可作渠道量级参考' if not reasons else '仅供粗略量级参考，不适用于渠道承诺或内部裁决',
    }


def build_result(counts, seed, epsilon, sample_ref):
    epsilon = Decimal(epsilon)
    eps_stat = epsilon / STATISTIC_COUNT
    scale = 1.0 / float(eps_stat)  # 敏感度 1 / 每统计量 ε
    noisy, clamped_counts = {}, 0
    for index, name in enumerate(STAT_NAMES):
        value = counts[name] + laplace_sample(seed, index, scale)
        # 只发布取整后的加噪人数；逐项噪声量绝不外发，否则可反推出精确计数。
        rounded = int(math.floor(value + 0.5))
        if rounded < 0:
            clamped_counts += 1
        noisy[name] = max(0, rounded)
    clamped_activation = noisy['activated_a'] > noisy['exposed_a'] or noisy['activated_b'] > noisy['exposed_b']
    if noisy['activated_a'] > noisy['exposed_a']:
        noisy['activated_a'] = noisy['exposed_a']
    if noisy['activated_b'] > noisy['exposed_b']:
        noisy['activated_b'] = noisy['exposed_b']
    half_width = round(LAPLACE_95 * scale, 2)
    return {
        'schema': SCHEMA,
        'version': VERSION,
        'sample_ref': sample_ref,
        'counts': noisy,
        'noise': {
            'mechanism': MECHANISM,
            'distribution': 'Laplace(0, sensitivity / (epsilon / 4))',
            'privacy': 'pure ε-differential privacy; sequential composition of 4 counts',
            'epsilon': float(epsilon),
            'per_statistic_epsilon': float(eps_stat),
            'sensitivity': 1,
            'scale': round(scale, 6),
            'confidence': CONFIDENCE,
            'error_half_width': half_width,
            'error_note': f'每个计数独立加噪，95% 双侧误差约 ±{half_width} 人（b·ln20）；人数为整数且已截断到非负。',
            'contribution_limit': '每名参与者对每个计数最多贡献 1；激活只计已曝光用户；一人仅限一组。',
            'randomness': '服务端密钥化伪随机（HMAC-SHA256 派生）抽样；对不掌握 DP_FINGERPRINT_KEY 的接收方等价于独立 Laplace 噪声。种子随任务持久化，重试、队列恢复与重复领取都不改变噪声。',
            'rounding': '四舍五入取整，负值截断为 0，激活人数不超过曝光人数（均为后处理）。',
        },
        'availability': _availability(noisy, half_width, clamped_counts, clamped_activation),
        'notice': NOTICE,
        'release_scope': '仅渠道汇总：加噪人数与可用性提示；不含用户标识、原始事件、内部裁决字段。',
    }


def _enqueue(redis_module, release_id):
    redis_module.Redis.from_url(os.environ['REDIS_URL']).rpush('trial:privacy', release_id)


def claim(s, redis_module, exp, user):
    """领取冻结样本的差分汇总。同一指纹返回原任务且不重复扣费。"""
    budget = budget_for(s, exp.id)
    counts, fingerprint, event_count = aggregate(s, exp)
    existing = s.query(DpRelease).filter_by(experiment_id=exp.id, sample_fingerprint=fingerprint).first()
    if existing:
        return existing, True
    if event_count == 0:
        # 空样本无汇总意义，直接拒绝且不扣额度。
        raise EmptySample('该实验尚无事件，没有可冻结的样本；请先核对事件再领取')
    # 原子条件扣减：金额取数据库当前配置（列表达式），避免配置并发变更造成超支或错扣；
    # 行级写锁使并发领取串行化，条件保证 used+per <= total 才扣。
    charged = s.execute(
        update(PrivacyBudget)
        .where(
            PrivacyBudget.experiment_id == exp.id,
            PrivacyBudget.used_epsilon + PrivacyBudget.epsilon_per_release <= PrivacyBudget.total_epsilon,
        )
        .values(
            used_epsilon=PrivacyBudget.used_epsilon + PrivacyBudget.epsilon_per_release,
            updated_at=now(),
        )
    )
    if charged.rowcount != 1:
        s.rollback()
        # 并发同样本：另一请求可能刚扣费落库，此时应返回其既有任务而非误报预算不足。
        existing = s.query(DpRelease).filter_by(experiment_id=exp.id, sample_fingerprint=fingerprint).first()
        if existing:
            return existing, True
        raise BudgetExhausted('隐私预算不足，拒绝外发；请由负责人评估后再调整预算')
    s.refresh(budget)
    epsilon = Decimal(budget.epsilon_per_release)
    row = DpRelease(
        experiment_id=exp.id,
        sample_fingerprint=fingerprint,
        sample_ref=fingerprint[:12].upper(),
        status='queued',
        epsilon=epsilon,
        noise_seed=derive_seed(fingerprint, epsilon),
        aggregate={'counts': counts, 'event_count': event_count, 'fingerprint': fingerprint},
        requested_by=user.email,
        attempts=0,
        created_at=now(),
    )
    s.add(row)
    try:
        s.commit()
    except IntegrityError:
        # 并发同样本：另一请求先落库；回滚本次扣费并返回既有任务。
        s.rollback()
        existing = s.query(DpRelease).filter_by(experiment_id=exp.id, sample_fingerprint=fingerprint).first()
        if existing:
            return existing, True
        raise
    try:
        _enqueue(redis_module, row.id)
    except Exception as exc:  # 扣费已提交，保持可恢复：标记失败但保留额度与噪声种子
        row.status = 'failed'
        row.error = f'队列暂不可用，可稍后重试且不会重复扣费：{exc.__class__.__name__}'
        s.commit()
    return row, False


def process_release(s, release):
    """worker 侧确定性计算：只读持久化的聚合与种子，重试结果不变。"""
    if release.status not in ('queued', 'running'):
        return release
    release.status = 'running'
    release.started_at = release.started_at or now()
    release.attempts = (release.attempts or 0) + 1
    s.commit()
    try:
        aggregate_data = release.aggregate or {}
        counts = aggregate_data.get('counts') or {}
        if not release.noise_seed:
            # 兼容旧行：种子缺失时按样本指纹补派生，仍保持确定性。
            release.noise_seed = derive_seed(release.sample_fingerprint, release.epsilon)
        release.result = build_result(counts, release.noise_seed, release.epsilon, release.sample_ref)
        release.status = 'done'
        release.error = ''
        release.completed_at = now()
    except Exception as exc:
        release.status = 'failed'
        release.error = f'{exc.__class__.__name__}: {exc}'
    s.commit()
    return release


def retry(s, redis_module, release):
    if release.status == 'done':
        raise ValueError('该任务已完成，无需重试')
    release.status = 'queued'
    release.error = ''
    release.started_at = ''
    s.commit()
    try:
        _enqueue(redis_module, release.id)
    except Exception as exc:
        release.status = 'failed'
        release.error = f'队列暂不可用：{exc.__class__.__name__}'
        s.commit()
    return release


def public(row, include_request=True):
    data = {
        'id': row.id,
        'experiment_id': row.experiment_id,
        'sample_ref': row.sample_ref,
        'status': row.status,
        'epsilon': float(row.epsilon),
        'attempts': row.attempts,
        'error': row.error or '',
        'created_at': row.created_at,
        'completed_at': row.completed_at or '',
        'result': row.result,
    }
    if include_request:
        data['requested_by'] = row.requested_by
        data['sample_fingerprint'] = row.sample_fingerprint
    return data


def external(row, exp):
    """外发文件：仅加噪人数、噪声机制/误差与可用性提示。"""
    if row.status != 'done' or not row.result:
        raise ValueError('任务尚未完成，不能外发')
    result = row.result
    return {
        'schema': result['schema'],
        'version': result['version'],
        'experiment_code': exp.code,
        'sample_ref': result['sample_ref'],
        'counts': result['counts'],
        'noise': result['noise'],
        'availability': result['availability'],
        'notice': result['notice'],
        'release_scope': result['release_scope'],
    }
