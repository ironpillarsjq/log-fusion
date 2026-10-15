# 服务器部署与更新

> 面向在 Linux 服务器上以 systemd 运行 log-fusion、并需要在页面上「一键拉取最新代码」的场景。
> 相关文件：`deploy/log-fusion.service`、`deploy/update-log-fusion.sh`、接口 `POST /api/v1/admin/update`。

## 1. 目录约定

更新脚本与 systemd 单元都按下面的固定路径工作，请保持一致（不一致时改脚本顶部的配置块）：

| 路径 | 说明 | 更新时 |
|---|---|---|
| `/opt/log-fusion` | 仓库根（`git clone` 建出的 git 工作树） | **原地 `git reset --hard origin/main`** |
| `/opt/log-fusion/.venv` | 虚拟环境 | 不动（被 `.gitignore` 忽略） |
| `/opt/log-fusion/backend/.env` | 环境配置（不在 git 里） | 不动（被 `.gitignore` 忽略） |
| `/opt/log-fusion/data`、`/opt/log-fusion/backend/data` | 归档等运行期产物（不在 git 里） | 不动（被 `.gitignore` 忽略） |

> `config.py` 的 `archive_dir` 默认是相对路径 `data/archive`，而工作目录是 `backend/`，
> 所以实际归档可能落在 `backend/data/`。这两个位置都在 `.gitignore` 里，原地更新不会碰它们。

## 2. 首次部署：把服务注册成 systemd 单元

```bash
# 1) 取代码
sudo git clone https://github.com/ironpillarsjq/log-fusion.git /opt/log-fusion
cd /opt/log-fusion

# 2) 建虚拟环境并装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3) 写环境配置（数据库地址等；该文件不进 git）
sudo tee backend/.env >/dev/null <<'EOF'
DB_HOST=192.168.5.6
DB_PORT=3306
DB_USER=root
DB_PASSWORD=改成你的密码
EOF

# 4) 安装并启动 systemd 单元
sudo cp deploy/log-fusion.service /etc/systemd/system/log-fusion.service
sudo systemctl daemon-reload
sudo systemctl enable --now log-fusion
systemctl status log-fusion
curl -fsS http://127.0.0.1:8080/health
```

单元要点（见 `deploy/log-fusion.service`）：

- `WorkingDirectory=/opt/log-fusion/backend`、`Environment=PYTHONPATH=/opt/log-fusion/backend`，
  与手工执行 `cd /opt/log-fusion/backend && PYTHONPATH=... python run.py` 等价；
- `Restart=always`：进程异常退出后自动拉起；
- `KillSignal=SIGINT`：等价终端 Ctrl+C，让 uvicorn 优雅退出；
- 以 root 运行（更新脚本需要 `systemctl`、覆盖 `/opt` 与写 `/root`）。

以后就用 `systemctl start / stop / restart / status log-fusion` 管理服务，不用再挂着一个 SSH 前台终端。

## 3. 日常更新代码

### 方式 A：页面上点按钮

「系统维护」页 → 「⇩ 拉取最新代码并重启服务」→ 二次确认。前端会立即返回、随后轮询 `/health`，
服务恢复后自动刷新页面。

### 方式 B：SSH 手工执行

```bash
sudo bash /opt/log-fusion/deploy/update-log-fusion.sh
# 日志：tail -f /var/log/log-fusion-update.log
```

### 更新脚本做什么

1. 校验 `/opt/log-fusion` 是 git 工作树（不是就中止，并提示按 §2 用 `git clone` 首次部署）；
2. 如果服务器上存在**对已跟踪文件的未提交改动**（约定：代码只在本机改，这里正常应为空），先备份成 patch 到 `/root/lf-keep/pre-update-<时间戳>.patch`；
3. `sleep 3` 让触发更新的 HTTP 响应先返回、页面上能看到「更新中」；
4. `systemctl stop log-fusion`，等 8080 端口释放；
5. `git fetch --prune origin main`（带超时兜底），随后 **`git reset --hard origin/main`**，让代码与远端严格一致；
6. 仅当 `requirements.txt` 在两个版本之间有变化时，才执行 `.venv/bin/pip install -r requirements.txt`；
7. `systemctl start log-fusion`，最多等 30 秒轮询 `/health` 判定是否恢复。

并用 `flock` 保证同一时间只有一次更新在跑。

### 为什么可以「原地更新」而不用删目录重 clone

`.venv`、`backend/.env`、`data/`、`backend/data/` 全部写在仓库的 `.gitignore` 里，而 **git 从不修改未跟踪/被忽略的文件**。所以 `git reset --hard` 只会改动仓库跟踪的代码文件，这些运行期文件天然被跳过——既不需要先搬走再搬回，也不会被覆盖。相比「删目录重 clone」，这样更快（增量拉取）、保留 `.git` 历史，也不需要搬移和回滚逻辑。

前提有两条，务必守住：

- **服务器只拉取、不修改代码。** 若有人在服务器上直接改了已跟踪文件，`git reset --hard` 会覆盖它（脚本会先备份 patch，但不要依赖这个）。要改代码请在本机改、推送到 GitHub。
- **绝不使用 `git clean`。** `git clean -fdx` 删掉的正是 `.env`、`data`、`.venv`——也就是 `.gitignore` 在保护的东西。脚本里只有 `fetch` 与 `reset --hard`，`tests/test_main_contracts.py` 也会断言脚本里不含 `git clean` / `rm -rf`。

另外：服务器上**未跟踪且未忽略**的文件（例如采集端配置目录 `backend/fluent-bit-config/`）不会被更新影响，脚本会把这些路径打印出来；只有当远端新增了同名路径的文件时，`reset` 才会覆盖/删除它。

### 为什么必须用 systemd-run（关键）

systemd 停止一个服务时会清理它**整条 cgroup**。如果更新脚本是后端进程直接 `fork` 出来的子进程，
`systemctl stop log-fusion` 会把脚本一起杀掉，更新半途中断——这也是不能简单用 `setsid`/`nohup` 绕过的原因
（`nohup` 只挡 SIGHUP，挡不住 cgroup 清理）。

因此接口用 `systemd-run --unit=log-fusion-update --collect` 起一个**独立的 transient 单元**，
它由 systemd 直接托管、不属于 `log-fusion` 的 cgroup，停服时不受影响。另外脚本在动手前会被复制到
仓库外的 `/usr/local/bin/log-fusion-update.sh` 再执行：`git reset --hard` 会重写仓库里的
`deploy/update-log-fusion.sh`，而 bash 是边读边执行脚本的，正在执行的脚本文件被改写会导致执行错乱。

## 4. 把更新脚本装到安全路径（可选）

接口每次都会把仓库内的 `deploy/update-log-fusion.sh` 复制到 `/usr/local/bin/log-fusion-update.sh`，
所以这一步不是必须的；但想在 SSH 里直接敲 `log-fusion-update.sh` 时可以先装一次：

```bash
sudo install -m 0755 /opt/log-fusion/deploy/update-log-fusion.sh /usr/local/bin/log-fusion-update.sh
```

## 5. 排查

| 现象 | 处理 |
|---|---|
| 页面点按钮报「服务器未安装 systemd-run」 | 服务器不是 systemd 发行版；改为 SSH 手工执行脚本 |
| 报「已有一次更新正在进行」 | 上一次更新还没结束；`systemctl status log-fusion-update` 查看 |
| 更新后服务没起来 | `journalctl -u log-fusion -n 100`、`tail -n 100 /var/log/log-fusion-update.log` |
| 更新卡住/失败残留单元 | `systemctl status log-fusion-update`；`sudo systemctl reset-failed log-fusion-update` |
| 消息里说「检测到未提交的已跟踪改动」 | 服务器被人直接改过代码；那次改动已备份到 `/root/lf-keep/pre-update-*.patch`，需要时 `git apply` 还原 |
| 服务器本地提交/历史与远端不一致 | 脚本用 `reset --hard origin/main` 强制对齐，不做合并；只要确认能连上 GitHub 且分支名正确即可 |
| 想确认服务是否恢复 | `curl -fsS http://127.0.0.1:8080/health` |

## 6. 安全提醒

`POST /api/v1/admin/update` **没有任何鉴权**（与项目其它接口一致）。任何能访问本服务的人都能触发一次
停服 + 覆盖代码 + 重启。**只应部署在受控内网/测试环境**。若要暴露到不可信网络，必须先加鉴权
（共享令牌或来源 IP 白名单），并考虑给该接口单独限流。
