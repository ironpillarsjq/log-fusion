#!/usr/bin/env bash
#
# log-fusion systemd 服务安装脚本（一次性；可重复执行）
#
# 作用：把仓库里的 deploy/log-fusion.service 安装成系统服务 log-fusion，
#      之后就能用 systemctl start/stop/restart/status log-fusion 管理；
#      这也是「一键更新」脚本（deploy/update-log-fusion.sh）能停服/起服的前提。
#
# 用法：sudo bash /opt/log-fusion/deploy/install-service.sh
#
# 幂等：单元内容没变时等于 daemon-reload + 重启；变了则先备份旧文件再覆盖。
# 注意：本脚本不负责拉代码。代码更新走 docs/DEPLOY.md §3（页面按钮或更新脚本）。

set -euo pipefail

# ============ 可按需修改的配置 ============
UNIT_NAME="log-fusion"
APP_DIR="/opt/log-fusion"
UNIT_SRC="$APP_DIR/deploy/log-fusion.service"
UNIT_DEST="/etc/systemd/system/log-fusion.service"
VENV_PYTHON="$APP_DIR/.venv/bin/python"
ENV_FILE="$APP_DIR/backend/.env"
PORT=8080
HEALTH_URL="http://127.0.0.1:${PORT}/health"
# =========================================

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "错误：$*"; exit 1; }

log "=== 安装 systemd 服务 $UNIT_NAME ==="
[ "$(id -u)" -eq 0 ] || die "需要 root 权限：sudo bash $APP_DIR/deploy/install-service.sh"
command -v systemctl >/dev/null 2>&1 || die "本机没有 systemctl，不是 systemd 发行版；请继续手工启动服务"
[ -f "$UNIT_SRC" ] || die "未找到单元文件 $UNIT_SRC（请先按 docs/DEPLOY.md 把代码取到服务器上）"
if [ ! -x "$VENV_PYTHON" ]; then
  die "未找到可执行的 $VENV_PYTHON，单元起不来。先建虚拟环境：
  python3 -m venv $APP_DIR/.venv
  $APP_DIR/.venv/bin/pip install -r $APP_DIR/requirements.txt"
fi
[ -f "$ENV_FILE" ] || log "警告：未找到 $ENV_FILE，服务可能缺少数据库配置"

# 端口被非本服务的进程占着（典型场景：还挂在 SSH 前台手工跑的 run.py）
if ss -ltn 2>/dev/null | grep -q ":${PORT} " && ! systemctl is-active --quiet "$UNIT_NAME"; then
  log "警告：${PORT} 已被占用且不是 $UNIT_NAME 服务，新服务将无法监听。"
  log "      请先停掉手工启动的进程（在那个终端按 Ctrl+C，或 pkill -f 'run.py'）"
fi

if [ -f "$UNIT_DEST" ] && ! cmp -s "$UNIT_SRC" "$UNIT_DEST"; then
  cp "$UNIT_DEST" "$UNIT_DEST.bak"
  log "单元内容有变化，旧文件已备份到 $UNIT_DEST.bak"
fi

install -m 0644 "$UNIT_SRC" "$UNIT_DEST"
log "已安装 $UNIT_DEST"

systemctl daemon-reload
systemctl enable "$UNIT_NAME" || die "systemctl enable 失败"
log "已设置开机自启"

systemctl restart "$UNIT_NAME" || die "systemctl restart 失败，请检查：journalctl -u $UNIT_NAME -n 50"
log "已启动服务"

if ! command -v curl >/dev/null 2>&1; then
  log "警告：未安装 curl，跳过 $HEALTH_URL 自检；请用 systemctl status $UNIT_NAME 确认"
  log "=== 安装完成 ==="
  exit 0
fi

# 健康检查：最多等 30 秒
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    log "服务已就绪：$HEALTH_URL 正常（状态 $(systemctl is-active "$UNIT_NAME")）"
    log "=== 安装完成 ==="
    exit 0
  fi
  sleep 1
done
log "警告：启动后 30 秒内 /health 未就绪，请检查：journalctl -u $UNIT_NAME -n 100"
exit 1
