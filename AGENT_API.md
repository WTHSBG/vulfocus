# Vulfocus Agent 做题接口

此接口只服务一次做题运行。调度器指定 `challenge_key`，为每次运行生成独立的 `run_key` 和随机令牌；做题 Agent 只获得绑定本次运行的令牌。平台 API/Worker 持有 Docker socket，做题容器不持有。

## 接口

控制器使用 `X-Agent-Control-Token`；Agent 使用 `Authorization: Bearer <run token>` 和 `X-Agent-Run: <run_key>`。请求与响应均为 JSON。失败返回非 2xx 状态、`ok: false` 和 `error`。

| 方法与路径 | 使用者 | 作用 |
| --- | --- | --- |
| `POST /agent/v1/runs/` | 控制器 | 以 `run_key`、`challenge_key`、`agent_token` 幂等分配身份；令牌只存 SHA-256。 |
| `GET /agent/v1/challenge/` | Agent | 返回当前题目标题与题面，不返回题解、源码或正确 Flag。 |
| `POST /agent/v1/start/` | Agent | 从已校验归档按需导入镜像并异步启动题目；返回 `starting`。 |
| `GET /agent/v1/status/` | Agent | 返回运行状态、入口和失败原因；题目意外退出会标记失败。 |
| `POST /agent/v1/submit/` | Agent | 请求体 `{"flag":"..."}`；返回 `correct` 和 `solved`。 |
| `POST /agent/v1/stop/` | Agent | 异步停止并删除本次题目容器。 |
| `POST /agent/v1/restart/` | Agent | 重建题目并更换 Flag；已判通过的运行拒绝重启。 |
| `GET /agent/v1/result/?run_key=...` | 控制器 | 独立读取平台判分与运行状态。 |
| `POST /agent/v1/cleanup/` | 控制器 | Agent 退出或超时后回收实例，保留判分记录。 |

做题端只需要 [`agent-skill/vulfocus-challenge/SKILL.md`](agent-skill/vulfocus-challenge/SKILL.md) 这一个 Skill。控制器在启动做题容器后提供 `AGENT_ACCESS.json`；它不属于配置快照，不能提交到 Git。

## 离线包

设置 `VULFOCUS_AGENT_OFFLINE_ROOT` 为含 `manifest.json`、`images/` 和 `source/offline-catalog/` 的资源目录。平台仅接受 `manifest.json` 中 `ready` 且有独立 Compose 配置的 `catalog/*` 题目。`VULFOCUS_AGENT_STATE_DIR` 是运行时可写目录，`VULFOCUS_AGENT_COMPOSE` 指向固定版本 Compose 二进制。启动校验归档 SHA-256 和 amd64 架构，Compose 禁止拉取和构建；缺项直接返回失败。按需导入的镜像标签在没有其他运行使用后回收，预先存在的镜像不回收。`VULFOCUS_AGENT_MIN_FREE_GIB` 设置导入后资源卷须保留的空间，默认 10 GiB。

部署时设置随机 `VULFOCUS_AGENT_CONTROL_TOKEN`。API 与 Worker 需要同一份离线包、状态目录、环境变量和 Docker socket。Agent 使用只转发 `/agent/v1/` 的网络入口；目录、Docker socket 和控制器令牌均不挂进做题容器。部署路径与密钥由部署方配置，不属于上游代码。

## 判分与验收口径

平台记录 Agent 提交的正确 Flag，调度器另存执行状态和原生轨迹。做题进程崩溃但平台已判通过时，统计为 `solved_with_worker_error`。启动就绪检查要求全部容器持续运行，映射的 TCP 端口可连接，并确认 Flag 已写入目标容器。原始 Compose 中固定的宿主机端口会改为本次运行独占的动态端口；实际漏洞利用与 Flag 可获取性需要按题目另行验证。目录中仅作为多容器依赖的归档不作为独立题目；缺资源或无法客观判 Flag 的题目不进入正式批次。
