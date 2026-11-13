# 原野交通抽样校准席 / sample-balance

为原野交通研究所的17位抽样分析员提供匿名出行样本的边际校准、权重诊断和复核交付。全部为合成数据；不用于个人决策。

上游为样本CSV和机构级目标边际；系统替代表格逐列手调权重，下游研究者使用获批权重和描述性加权均值。原有痛点：样本结构与目标人群不同、极端权重不可见、报告只给加权总数而忽略有效样本。该应用不负责问卷收集、投票或路线调度。

## 快速启动

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

访问 http://localhost:18219 ，API文档 http://localhost:19219/docs ，MySQL localhost:17719。五服务为frontend/backend/worker/db/redis，各有实际healthcheck；所有容器依赖数据库/Redis真实健康，worker用心跳。数据库、Redis和共享产物使用命名卷。中文及空格父目录可直接启动。清理本项目演示数据：`docker compose down -v --remove-orphans`。

| 角色 | 账号 | 密码 | 权限/主要动作 |
|---|---|---|---|
| admin 抽样负责人 | admin@balance.demo | Balance2026! | 所有页面；登记目标边际、审批权重、导出与审计 |
| analyst 交通分析师 | analyst@balance.demo | Balance2026! | 创建批次、CSV导入、运行校准、查看诊断与审计；不可改目标或批准 |
| viewer 研究阅读者 | viewer@balance.demo | Balance2026! | 样本/目标/结果只读，获批后CSV下载；无审计权限 |

## 一天的工作剧本

1. 09:00 分析师登录，检查“11月城区出行样本”的800条匿名观测，来源 `yy-travel-sample.csv`。
2. 检查分区、年龄组、出行方式与出行分钟数，不把编号作为统计特征。
3. 负责人登记总量均为10000的三套边际：分区30/45/25%、年龄35/45/20%、方式30/35/35%。
4. 分析师建立“11月城区出行·代表性校准”，容差0.0001、最多500次、截断上限为目标平均权重12倍。
5. 独立worker从Redis接任务，按类别调整权重并持久化结果，页面自动刷新。
6. 查看边际残差、迭代曲线、权重范围、Kish有效样本量及原始/加权平均出行时长。
7. 不收敛时调整兼容目标或上限，保留失败和诊断；负责人不能批准不收敛结果。
8. 负责人记录适用范围并批准，阅读者下载完整权重CSV；记录关联输入SHA256。

## 真实计算口径

实现位置 `backend/src/services/raking_algorithm.py`。

1. Raking/IPF：初始正权重归一到目标总量；按年龄组、方式、分区的固定顺序，用 target_category/current_category 乘法调整。每轮后按 cap_ratio × target_total / n 截断。误差是各类别绝对残差除共同目标总量，达到容差才声明收敛；截断不保持每轮总量，不可行目标可能不收敛，此时结果不能批准。此算法是有界迭代近似，不宣称约束最大熵的精确最优解。
2. Kish有效样本 `ESS=(Σw)^2/Σw²`；设计效应 `n/ESS`；加权均值 `Σw*y/Σw`。这些量只反映权重离散与描述性估计，不包含聚类/分层设计，不保证校准已消除未观测选择偏差，也不作因果解释。

参数：容差1e-6..0.05、迭代1..2000、截断倍数1..30，每批最多5000行。目标每维总量必须一致且正；重复目标、遗漏样本类别、正目标类别无样本支持、零权重支持、非有限/负值被拒绝。目标为零可使相应权重为零。所有影响结果的样本维度、初始权重、出行时长、边际与参数都进入SHA256，并按自然编号/类别排序；行序改变不改变结果。结果保存原始样本与目标诊断快照，后续目标变更不会回写历史运行。

独立4样本oracle：A组目标60、B组40，其余两维50/50，权重应为30/30/20/20；加权时长23、原始25、ESS10000/2600、设计效应1.04。cap_ratio=1不能满足目标，必须显示未收敛。

### 整数化交付

实现位置 `backend/src/services/integerization_service.py`。接收机构按整数人数交付，逐条四舍五入会破坏边际，因此整数化是一次全局约束求解，而不是逐列取整：

1. 硬约束：每个维度每个类别的整数权重合计精确等于整数目标，全部整数权重合计精确等于目标总人数（总人数约束由各维类别合计自然保证）。
2. 取值范围：放松参数 k=0 时每条整数权重只能取原小数权重的 floor 或 ceil；页面可把范围放宽到 [floor−k, ceil+k]（k∈0..20）重新求解，边际硬约束不放松。
3. 求解：纯标准库的边界一致性传播 + 回溯（二值域走增量计数快速路径，放松后的多值域走整数域分裂），确定性、不引入外部求解器；5 秒或节点预算用尽即超时，拒绝交付。
4. 三类无解原因互斥区分——`target_non_integer`（目标本身非整数，先把边际登记为整数）、`range_insufficient`（当前 k 内无解但宽范围可解，调大 k 即可，附紧张类别）、`structural_infeasible`（放宽到最大范围仍无解，属样本交叉结构与整数边际不兼容，调 k 无效）；超时 `timeout` 单独以 504 提示，与无解分开，不产出半成品。
5. 交付门控与留痕：仅已批准运行可整数化；admin/analyst 可发起，viewer 只读与下载。每次成功或失败按 (运行, k) 覆盖归档到 `integer_deliveries` 并写审计；只有 `solved` 归档能导出整数 CSV。原小数权重导出 (`/runs/{id}/export`) 口径不变、始终可用，整数导出为 `/runs/{id}/export-integer?relax=k`。页面展示逐类对账（整数合计 vs 整数目标、残差）与逐条权重改动（原值/floor/ceil/整数值/是否限于上下整数）。

## 页面与结构

六个研究页面：总览、样本底册、目标边际、校准配置/记录、诊断结果、复核交付（含有权角色的审计）。整数化交付面板 `IntegerDeliveryPanel` 内嵌于诊断结果页（获批后出现），含放松范围求解、三类无解与超时提示、逐类对账和权重改动。BatchSelect、RunTable、CalibrationSummary、MarginTable跨页复用；PageHeading/ErrorNotice/StatusBadge为公共组件；useTask/usePolling与格式工具共享。初始3批共1280条样本、27个目标格、一个实际计算的初始运行及获批记录，种子73/74/75。

前端：`src/{api,stores,types,components/common,hooks,pages,router,utils,constants}`；后端：`src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}`；种子在`backend/scripts/seed_demo.py`，结构约束见`backend/migrations/001_schema.md`。整数化相关：服务 `services/integerization_service.py`（求解）与 `services/integer_delivery_service.py`（门控/归档/失败映射）、模型 `models/integer_delivery.py`（新表 `integer_deliveries`，由 metadata 新建，不改既有表）、路由 `routes/integer_deliveries.py`、控制器与 `schemas/integerization.py`。测试、截图和TESTING只在批次`test-artifacts/gb-0015/`。

共享枚举Role/RunStatus/Dimension后端在`backend/src/constants/enums.py`，前端在`frontend/src/constants/enums.ts`；角色认证在middlewares/auth、auth store；状态用于run_service/worker/StatusBadge；Dimension用于target schema、raking维度与目标页。数据库字段采用相同枚举字符串。

## 环境与开发

Python3.11/FastAPI/SQLAlchemy/PyJWT，Vue3.5/TypeScript/Vite6.4.6，MySQL8、Redis7与Nginx1.27。本地API配置DATABASE_URL/REDIS_URL/JWT_SECRET后`uvicorn src.main:app --reload`；前端`npm ci && npm run dev`，/api代理19219。所有计算、校验及持久化在Python后端。

.env.example列出端口、数据库用户/密码、JWT密钥与SEED_DEMO；.env、node_modules、dist和日志被忽略。演示密码是公开本地账号。SEED_DEMO=true自动执行插入式自然键seed，不覆盖改动；失败记日志不阻塞服务。限流使用Redis每IP每分钟360次，统一错误响应并记录写操作审计。

单worker的Redis BLPOP演示队列无处理中崩溃自动重投；需重新提交。CSV为文本导入，整批验证后写入且不覆盖已有编号；类别文本导出已做公式前缀转义。审批后才可导出。首次schema由metadata创建，未实现生产迁移升级。字体依赖网络。详见真实TESTING中的范围和执行结果。

## License

MIT，见LICENSE。
