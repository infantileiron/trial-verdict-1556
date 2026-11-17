from pydantic import BaseModel, Field

class PrivacyClaimInput(BaseModel):
    experiment_id: int = Field(gt=0)

class PrivacyBudgetInput(BaseModel):
    # 保留 6 位小数，不接受 NaN/Inf，避免预算被非有限值绕过。
    total_epsilon: float = Field(gt=0, le=1000, allow_inf_nan=False)
    epsilon_per_release: float = Field(gt=0, le=1000, allow_inf_nan=False)
