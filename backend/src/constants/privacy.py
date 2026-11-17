"""差分隐私汇总发布的固定参数。

预算以 ε 计，采用纯 ε-DP（无 δ），避免把近似 DP 的口径混入渠道汇总。
每个发布冻结样本固定拆成四项人数统计（A/B × 曝光/激活），按顺序组合
各消耗 ε/4，因此一次领取的总隐私支出恰为单次额度。
"""
from decimal import Decimal

DEFAULT_TOTAL_EPSILON = Decimal('3.000000')
DEFAULT_EPSILON_PER_RELEASE = Decimal('1.000000')
STATISTIC_COUNT = 4
CONFIDENCE = 0.95
VERSION = 'laplace-counts-1.0'
MECHANISM = 'Laplace'
SCHEMA = 'prism-dp-aggregate-v1'
NOTICE = '本文件仅含加噪人数与可用性提示，用于渠道侧汇总参考；不含任何用户身份或原始事件明细，也不得作为内部上线裁决依据。'
