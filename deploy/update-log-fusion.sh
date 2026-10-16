#!/usr/bin/env bash
#
# log-fusion 服务器端一键更新脚本（原地 git 更新）
#
# 流程：停服 → 备份未提交改动 → git fetch + reset --hard origin/main
#      → 重装依赖 → 重启服务 → /health 自检
#
# 为什么用「原地 git 更新」而不是「删目录重新 clone」：
#   `.venv`、`backend/.env`、`data/`、`backend/data/` 都在 .gitignore 里，
#   git 从不改动未跟踪/被忽略的文件，所以它们既不会被覆盖，也无需搬来搬去。
#   代价是本脚本假设「代码只在本机改、服务器只拉取」：`git reset --hard` 会丢弃
#   服务器上对已跟踪文件的未提交改动（脚本会先把它们备份成 patch 到 $BACKUP_DIR）。
#
# 绝对不要在这里用 `git clean`：它会删掉 .env / data / .venv，
# 正好是 .gitignore 在保护的东西。
#
# 前提：$APP_DIR 必须是一个健康的 git 工作树（首次部署用 git clone 建好，见 docs/DEPLOY.md）。
#
# 两种执行方式：
#   1) 由后端接口 POST /api/v1/admin/update 通过 systemd-run 以**独立 cgroup** 启动；
#      这样 `systemctl stop log-fusion` 不会把本脚本一起杀掉（关键，见 docs/DEPLOY.md）。
#   2) 手工执行：sudo bash /usr/local/bin/log-fusion-update.sh
#
# 目录约定（与 deploy/log-fusion.service 一致）：
#   /opt/log-fusion            仓库根（git 工作树，原地更新）
#   /opt/log-fusion/.venv      虚拟环境（保留，本脚本不碰）
#   /opt/log-fusion/backend/.env  环境配置（保留，不在 git 里）
#   /opt/log-fusion/data       归档等运行期数据（保留，不在 git 里）

# -E 让下面的 ERR trap 在函数与命令替换里也生效
set -Eeuo pipefail

# ============ 可按需修改的配置 ============
REPO_URL="https://github.com/ironpillarsjq/log-fusion.git"
BRANCH="main"
APP_DIR="/opt/log-fusion"
BACKEND_DIR="$APP_DIR/backend"
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$BACKEND_DIR/.env"
REQUIREMENTS="$APP_DIR/requirements.txt"
SERVICE_NAME="log-fusion"
# 更新前把服务器上任何未提交改动备份到这里（正常应始终为空）
BACKUP_DIR="/root/lf-keep"
HEALTH_URL="http://127.0.0.1:8080/health"
PORT=8080
LOG_FILE="/var/log/log-fusion-update.log"
LOCK_FILE="/var/lock/log-fusion-update.lock"
# git fetch 超时（秒）：网络抖动时不至于一直占着锁把后续更新全堵住
FETCH_TIMEOUT=120
# =========================================

# 日志同时写 stdout（进 journal）与日志文件。
# 刻意不用 `exec > >(tee …)`：systemd 单元默认 KillMode=control-group，主进程一退出就会
# 连坐杀掉 tee；脚本若在很早期失败，日志可能连文件都来不及建，现场表现为「什么都没有」。
log() {
  local line="[$(date '+%F %T')] $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$LOG_FILE" 2>/dev/null || true
}
die() { log "错误：$*"; exit 1; }

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE" 2>/dev/null || true

# 任何未预期的失败都留下「退出码 + 具体命令」，避免再出现无从排查的静默死亡
trap 'log "更新失败：退出码 $?，失败命令：$BASH_COMMAND"' ERR

# 互斥：接口与手工执行可能并发触发，用 flock 保证同一时间只有一次更新
exec 9>"$LOCK_FILE"
flock -n 9 || die "已有一次更新正在进行（锁 $LOCK_FILE）"

log "=== log-fusion 更新开始 ==="
[ "$(id -u)" -eq 0 ] || die "需要 root 权限执行"
command -v git >/dev/null 2>&1 || die "服务器未安装 git"
[ -d "$APP_DIR/.git" ] || die "$APP_DIR 不是 git 工作树（请按 docs/DEPLOY.md §2 用 git clone 首次部署）"
[ -d "$VENV_DIR" ] || die "未找到虚拟环境 $VENV_DIR（请确认部署路径）"
[ -f "$ENV_FILE" ] || log "警告：未找到 $ENV_FILE，服务可能缺少数据库配置"

# 一律先 cd 进仓库再用 git，**不用 `git -C`**：CentOS 7 自带的 git 1.8.3 不认识 -C，
# 会以「Unknown option: -C」退出 129（git 的用法错误码），脚本当场中断。
cd "$APP_DIR" || die "无法进入 $APP_DIR"
git rev-parse --git-dir >/dev/null 2>&1 || die "$APP_DIR 不是可用的 git 工作树"

# `git remote get-url` 需要 git 2.7+，用 `config --get` 兼容老版本
log "仓库地址 $(git config --get remote.origin.url 2>/dev/null || echo '未配置')"
OLD_HEAD="$(git rev-parse HEAD)"
log "当前版本 $OLD_HEAD"

# 未被跟踪、也未被忽略的文件（如采集端配置目录）：git reset 不会动它们，
# 只有远端新增同名路径时才会被覆盖/删除，先列出来让人心里有数。
UNTRACKED="$(git status --porcelain --untracked-files=normal | awk '/^\?\?/{print $2}')"
if [ -n "$UNTRACKED" ]; then
  log "提示：以下未跟踪文件不受更新影响：$(echo "$UNTRACKED" | tr '\n' ' ')"
fi

# 让触发更新的 HTTP 响应先返回、页面上能看到「更新中」，再动服务
sleep 3

# 1) 停止服务
log "停止服务 $SERVICE_NAME ..."
systemctl stop "$SERVICE_NAME" || true
for _ in $(seq 1 30); do
  if ! ss -ltn 2>/dev/null | grep -q ":${PORT} "; then break; fi
  sleep 1
done

# 2) 备份服务器上未提交的已跟踪改动（约定：服务器只读，正常这里应为空）
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  mkdir -p "$BACKUP_DIR"
  PATCH="$BACKUP_DIR/pre-update-$(date '+%Y%m%d-%H%M%S').patch"
  if git diff HEAD >"$PATCH"; then
    log "警告：检测到未提交的已跟踪改动，已备份到 $PATCH（这些改动将被覆盖）"
  else
    log "警告：备份未提交改动失败（$PATCH），仍继续更新"
  fi
fi

# 3) 拉取远端分支（网络抖动时有超时兜底，不至于一直占着锁）
#
# refspec 显式写成 `+refs/heads/<branch>:refs/remotes/origin/<branch>`：
# 若只写 `git fetch --prune origin main`（无冒号），是否顺带更新 origin/main 取决于该仓库
# 有没有配 `remote.origin.fetch`；配置缺失时只写 FETCH_HEAD，那么下面 `rev-parse origin/main`
# 读到的是克隆时的旧 ref，脚本会误判「远端无新提交」而永远不更新（静默失效）。
FETCH=(git fetch --prune origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH")
if command -v timeout >/dev/null 2>&1; then
  FETCH=(timeout "$FETCH_TIMEOUT" "${FETCH[@]}")
fi
log "拉取 $REPO_URL 分支 $BRANCH ..."
if ! "${FETCH[@]}"; then
  log "git fetch 失败，直接拉起当前版本"
  systemctl start "$SERVICE_NAME" || true
  die "更新中止（代码目录未改动）"
fi

NEW_HEAD="$(git rev-parse "origin/$BRANCH")"

# 4) 对齐到远端分支（只 reset；.env / data / .venv 靠 .gitignore 保护，绝不 git clean）
if [ "$NEW_HEAD" = "$OLD_HEAD" ]; then
  # 与本次 fetch 的实际结果核对：不一致说明本地远端引用没被更新（配置问题），
  # 此时「无新提交」是假象——必须报错，而不是静默跳过让按钮假装成功。
  if [ "$(git rev-parse FETCH_HEAD)" != "$NEW_HEAD" ]; then
    log "错误：origin/$BRANCH=$NEW_HEAD 与本次 fetch 的实际结果（$(git rev-parse FETCH_HEAD)）不一致"
    systemctl start "$SERVICE_NAME" || true
    die "更新中止：本地远端引用未被更新，请检查 cd $APP_DIR && git config --get-all remote.origin.fetch"
  fi
  log "远端无新提交，跳过代码替换（$OLD_HEAD）"
else
  git reset --hard "origin/$BRANCH" || {
    log "git reset --hard 失败（常见原因：有未跟踪文件挡住了远端新增的同名路径）"
    systemctl start "$SERVICE_NAME" || true
    die "更新中止（代码可能处于中间状态，请人工检查 $APP_DIR）"
  }
  log "代码已更新 ${OLD_HEAD} -> ${NEW_HEAD}"

  # systemd 单元变了不会自动生效，提醒重跑安装脚本（本脚本不替它做主）
  if ! git diff --quiet "$OLD_HEAD" "$NEW_HEAD" -- deploy/log-fusion.service; then
    log "提示：deploy/log-fusion.service 在本次更新中有变化，" \
        "请重跑 sudo bash $APP_DIR/deploy/install-service.sh 同步单元"
  fi

  # 5) 重装依赖（幂等；requirements.txt 有变化时才真去装）
  if [ -f "$REQUIREMENTS" ]; then
    if git diff --quiet "$OLD_HEAD" "$NEW_HEAD" -- requirements.txt; then
      log "requirements.txt 无变化，跳过依赖安装"
    elif [ -x "$VENV_DIR/bin/pip" ]; then
      log "requirements.txt 有变化，安装依赖 ..."
      PYTHONPATH="$BACKEND_DIR" "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS" \
        || log "警告：依赖安装返回非零，仍继续尝试启动"
    else
      # 有些服务器的 venv 是 --without-pip 建的（bin/pip 不存在）。此时绝不能静默跳过，
      # 否则新代码缺少依赖会以「启动失败」收场，而日志里看不到真正原因。
      log "警告：requirements.txt 在本次更新中有变化，但 $VENV_DIR/bin/pip 不存在，依赖未安装！"
      log "      修复后重试：$VENV_DIR/bin/python -m ensurepip --upgrade"
    fi
  fi
fi

# 6) 启动服务
log "启动服务 $SERVICE_NAME ..."
systemctl start "$SERVICE_NAME" || log "警告：systemctl start 返回非零，继续等待 /health"

# 7) 健康检查：最多等 30 秒
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    log "服务已恢复：$HEALTH_URL 正常"
    log "当前运行版本 $(git log --oneline -1)"
    log "=== 更新完成 ==="
    exit 0
  fi
  sleep 1
done
log "警告：启动后 30 秒内 /health 未就绪，请检查：journalctl -u $SERVICE_NAME -n 100"
exit 1
