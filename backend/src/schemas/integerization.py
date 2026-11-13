from pydantic import BaseModel, Field


class IntegerizeInput(BaseModel):
    run_id: int = Field(gt=0)
    # 放松范围 k：每条权重允许落在 [floor-k, ceil+k]；0 表示严格限于上下整数
    relax: int = Field(default=0, ge=0, le=20)
