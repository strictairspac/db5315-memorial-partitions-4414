# 初始结构与启动升级

`src/models/tables.py` 定义初始关系结构。API 启动时显式创建演示库缺失的表；已有表不会被重建，已签证据的报告与校验码也不会改写。

保留截止日方案沿用演示库：新增 `retention_plans` 表，并给 `business_evidence` 增加可空的 `plan_id`、放开 `run_id` 非空限制。旧演示卷由 `src/services/schema_upgrade.py` 在启动时幂等补齐这两处差异，见 `upgrade()`。正式部署需要独立的版本化迁移时，应在此之外引入 Alembic 迁移，并把本文件中的启动期升级替换掉。
