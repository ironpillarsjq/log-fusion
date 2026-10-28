# 服务器部署与更新

> 面向在 Linux 服务器上以 systemd 运行 log-fusion、并需要在页面上「一键拉取最新代码」的场景。
> 相关文件：`deploy/log-fusion.service`、`deploy/install-service.sh`（注册服务）、`deploy/update-log-fusion.sh`（更新代码）、接口 `POST /api/v1/admin/update`。

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

# 4) 安装并启动 systemd 单元（脚本会做校验、daemon-reload、enable、restart 与 /health 自检）
sudo bash deploy/install-service.sh
systemctl status log-fusion
```

`deploy/install-service.sh` 是**幂等**的：单元没变时等于 `daemon-reload` + 重启；内容有变化时先把旧文件备份成
`/etc/systemd/system/log-fusion.service.bak` 再覆盖。所以以后只要 `deploy/log-fusion.service` 被改过，
重跑一次它就行——更新脚本检测到该文件在本次更新里有变化时也会打印这条提示。

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

**必须先把脚本复制到仓库之外再执行**——脚本会 `git reset --hard`，可能重写仓库里正在被 bash 读取的自己：

```bash
sudo cp /opt/log-fusion/deploy/update-log-fusion.sh /usr/local/bin/log-fusion-update.sh
sudo bash /usr/local/bin/log-fusion-update.sh
# 日志：tail -f /var/log/log-fusion-update.log
```

### 更新脚本做什么

1. 校验 `/opt/log-fusion` 是 git 工作树（不是就中止，并提示按 §2 用 `git clone` 首次部署）；
2. 如果服务器上存在**对已跟踪文件的未提交改动**（约定：代码只在本机改，这里正常应为空），先备份成 patch 到 `/root/lf-keep/pre-update-<时间戳>.patch`；
3. `sleep 3` 让触发更新的 HTTP 响应先返回、页面上能看到「更新中」；
4. `systemctl stop log-fusion`，等 8080 端口释放；
5. `git fetch --prune origin '+refs/heads/main:refs/remotes/origin/main'`（带超时兜底；refspec 显式写到 `refs/remotes/origin/main`，否则仓库缺 `remote.origin.fetch` 时只写 `FETCH_HEAD`、`origin/main` 不更新，会误判「远端无新提交」），随后 **`git reset --hard origin/main`**，让代码与远端严格一致；
6. 仅当 `requirements.txt` 在两个版本之间有变化时，才执行 `.venv/bin/pip install -r requirements.txt`；
7. 若 `deploy/log-fusion.service` 在本次更新里有变化，打印提示让你重跑 `sudo bash deploy/install-service.sh`（脚本不会替你改系统单元）；
8. `systemctl start log-fusion`，最多等 30 秒轮询 `/health` 判定是否恢复。

并用 `flock` 保证同一时间只有一次更新在跑。脚本**自己直接追加写** `/var/log/log-fusion-update.log`（不用 `exec > >(tee …)`：systemd 默认 `KillMode=control-group`，主进程一退出就连坐杀掉 `tee`，早期失败会连日志文件都来不及建），并且装了 `ERR` trap，任何未预期的失败都会在日志里留下**退出码和失败命令**。

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

## 4. 把更新脚本装到仓库外

页面按钮走的方式 A 会自动做这件事（接口每次都把仓库内的脚本复制到 `/usr/local/bin/log-fusion-update.sh` 再执行）。
**SSH 手工执行前必须先装一次**，原因见上文「方式 B」：不能在仓库内直接跑自己。

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
| 页面点按钮报 500 且提到 systemd | 服务还没注册；先 `sudo bash /opt/log-fusion/deploy/install-service.sh` |
| 更新单元**秒退**、`code=exited, status=129`、日志里一行都没有 | 服务器 git 太老（CentOS 7 自带 1.8.3）不支持 `git -C`，git 会以用法错误码 129 退出。脚本已改为「先 `cd` 再用 `git`」，把脚本更新到最新即可；`git --version` 可确认版本 |
| 更新单元今天失败过、但 `systemctl show log-fusion-update` 显示 `Result=success` | `systemctl reset-failed log-fusion-update` 会把失败状态清成默认值；判断有没有跑成功要以 `/var/log/log-fusion-update.log` 和 `git log -1` 为准 |
| 安装脚本报「8080 已被占用且不是 log-fusion 服务」 | 手工启动的 `run.py` 还挂在 SSH 前台；先 Ctrl+C 或 `pkill -f 'run.py'` 再重跑安装脚本 |
| 安装脚本报「未找到可执行的 /opt/log-fusion/.venv/bin/python」 | 虚拟环境没建或路径不对；按 §2 第 2 步建好再装单元 |
| 提示 `deploy/log-fusion.service` 有变化 | 单元文件改了但系统里还是旧的；重跑 `sudo bash deploy/install-service.sh` 同步 |
| 消息里说「检测到未提交的已跟踪改动」 | 服务器被人直接改过代码；那次改动已备份到 `/root/lf-keep/pre-update-*.patch`，需要时 `git apply` 还原 |
| 服务器本地提交/历史与远端不一致 | 脚本用 `reset --hard origin/main` 强制对齐，不做合并；只要确认能连上 GitHub 且分支名正确即可 |
| 想确认服务是否恢复 | `curl -fsS http://127.0.0.1:8080/health` |

## 6. 安全提醒

`POST /api/v1/admin/update` **没有任何鉴权**（与项目其它接口一致）。任何能访问本服务的人都能触发一次
停服 + 覆盖代码 + 重启。**只应部署在受控内网/测试环境**。若要暴露到不可信网络，必须先加鉴权
（共享令牌或来源 IP 白名单），并考虑给该接口单独限流。


## WAF/DAS Syslog 随主服务运行（2026-10-28）

现有 log-fusion.service 执行 run.py 时，FastAPI lifespan 默认一起启动 WAF 5514 和 DAS 5515 TCP/UDP，无需额外注册 Syslog 服务。端口、来源和开关放 backend/.env，见 [SYSLOG.md](SYSLOG.md)。仍须单 worker；若以前手动运行过独立接收 CLI，先停止，避免启动端口冲突。需要另外开放相应 TCP/UDP 防火墙入站，原服务启动脚本对 HTTP 8080 的规则不能代替 Syslog 规则。六张业务表的 SQL 仍须显式部署，未建表时先落本地缓冲。

网页更新/重载/重启会同时短暂停止 Syslog 监听，已提交缓冲保留并在启动后续写，未收到的 UDP 无法补救。若启用的端口不可绑定，整个应用启动失败并记录对应产品和端口；先修改配置或解决占用，再重启。设置 SYSLOG_ENABLED=false 可明确关闭接收，仅启动原 Web 服务。
