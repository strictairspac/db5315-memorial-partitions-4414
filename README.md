# 松门纪念园·入园凭条分区

真实 PostgreSQL 范围分区分流、迟到凭条回补并核验保留期边界。面向演示数据和本地复现，场景、操作剧本和业务边界见 [BUSINESS.md](BUSINESS.md)。

## 业务模块

- 凭条账簿
- 分区边界
- 方案预览
- 生命周期执行
- 回收核验

保留截止日调整先经「方案预览」看清分区目录、在线凭条和已摘除凭条的影响，再确认生效；生效时挂载、摘除和逐张对账一次完成，失败保留旧版状态与已签证据。

## 本地运行

```sh
cp -n .env.example .env
docker compose up -d --build --wait
```

- 工作台 `http://127.0.0.1:18304`
- FastAPI 文档 `http://127.0.0.1:19304/docs`
- PostgreSQL 16 端口 `127.0.0.1:17804`，账号 `app`，演示库 `workflow`
- `docker compose exec -T backend python -m scripts.seed_demo` 手动重跑演示数据
- `docker compose logs -f backend worker` 查看运行情况
- `docker compose down` 停容器并保留演示卷

### 演示账号

统一密码 `Demo-Walkthrough-2026`。

| 账号 | 角色 | 能执行的操作 |
|---|---|---|
| `operator` | 经办员 | 读取档案、建立或调整源数据、预览并确认方案、导出方案与证据 |
| `reviewer` | 复核员 | 读取、导出和签署证据 |
| `viewer` | 观察员 | 读取方案与已有证据、导出 |

`SEED_DEMO=true` 启动时生成账号与一个业务样例。工作台新建档案会预填样例。可编辑表单中的数值、文本和结构化 JSON 后提交，提交后进入方案预览，确认生效才挂载、摘除。业务说明中的原始文件是上游示例，演示数据通过表单录入。角色、执行与方案状态枚举位于后端 `src/constants/workflow.py` 与前端 `src/constants/workflow.js`。证据与方案校验码均为 JSON 按 UTF-8、键排序且省略多余空格序列化后的 SHA-256。

方案以（档案、源数据、基线已签证据）去重：相同条件下重复预览或重复生效只返回原方案，不新增版本或证据。权威对照基线是上一版已生效方案保存的逐张去向。旧版本数据库卷会在启动时自动补齐方案相关列，已签证据的报告与校验码不改写。

## 工程结构

| 服务 | 实现 |
|---|---|
| frontend | Nginx 与原生 HTML、CSS、JavaScript |
| backend | Python 3.11、FastAPI、SQLAlchemy |
| worker | Python 持久队列执行与 Redis 唤醒信号 |
| db | PostgreSQL 16，持久卷保存档案、执行、证据及审计 |
| redis | Redis 7，追加日志卷保存队列唤醒信号 |

编辑 `backend/src/services/domain.py` 可修改分区试算、预览对照与业务表单说明；方案版本、失效判定与生效事务位于 `backend/src/services/plans.py`。API 路由、数据定义和页面位于 `backend/src/routes/`、`backend/src/models/` 与 `frontend/src/pages/`。开发时更新源文件后重新构建对应服务。

后端集成测试在 `backend/tests/`，需要可写的 PostgreSQL 演示库（挂载/摘除使用临时分区）。在 `backend` 目录执行：

```sh
pip install -r requirements.txt pytest httpx
DB_HOST=127.0.0.1 DB_PORT=17804 DB_USER=app DB_PASSWORD=local-db-demo DB_NAME=workflow \
  AUTH_KEY=test PYTHONPATH=. pytest tests -q
```

测试为每个用例新建独立 schema 并在结束后删除，不会改动工作台数据。

`.env.example` 提供 `FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`DB_PASSWORD`、`AUTH_KEY` 、`SEED_DEMO` 与 `NETWORK_SUBNET`。修改 `NETWORK_SUBNET` 可避开本机现有容器网段。所有发布的端口绑定本机回环地址。接入独立部署前需配置自己的凭据并按业务范围做独立验收。

[MIT License](LICENSE)
