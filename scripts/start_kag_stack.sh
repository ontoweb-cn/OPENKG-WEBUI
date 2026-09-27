#!/usr/bin/env bash
# 启动 KAG 全栈：OpenSPG server（8887）+ kag-bridge（8890，streamable-http）。
#
# 前置条件（脚本只负责"拉起"，不负责首次部署）：
#   1. Docker/OrbStack 可用；OpenSPG 容器栈此前已创建（详见 docs/kag-integration-design.md
#      §5.1 部署模型）——本脚本只 start 既有容器，不重建 compose。
#   2. KAG 源码 venv 已装 kag 与 kag-bridge（pip install -e kag-bridge）。
#
# 用法：
#   LLM_API_KEY=gpustack_xxx scripts/start_kag_stack.sh
#
# 可覆盖的环境变量：
#   LLM_API_KEY          KAG kag_config.yaml 的 !ENV 引用（GPUStack 网关 key）。
#                        未设置时仅告警：bridge 仍可起（懒加载 KAG），但工具调用会失败。
#   KAG_DIR              KAG 项目目录（含 kag_config.yaml），默认 <repo>/../KAG
#   KAG_PYTHON           KAG venv 解释器，默认 $KAG_DIR/.venv/bin/python
#   KAG_BRIDGE_PORT      bridge 端口，默认 8890
#   KAG_BRIDGE_HOST      bridge 绑定地址，默认 127.0.0.1
#   KAG_BRIDGE_KEY_FILE  bridge API key 文件，默认 <repo>/data/system/kag-bridge-api-key
#   SPG_SERVER_CONTAINER OpenSPG server 容器名，默认 release-openspg-server
#   SPG_DEPS_CONTAINERS  依赖容器名（空格分隔），默认 release-openspg-mysql
#                        release-openspg-neo4j release-openspg-minio
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KAG_DIR="${KAG_DIR:-$REPO_ROOT/../KAG}"
if [ -d "$KAG_DIR" ]; then KAG_DIR="$(cd "$KAG_DIR" && pwd)"; fi
KAG_PYTHON="${KAG_PYTHON:-$KAG_DIR/.venv/bin/python}"
KAG_BRIDGE_HOST="${KAG_BRIDGE_HOST:-127.0.0.1}"
KAG_BRIDGE_PORT="${KAG_BRIDGE_PORT:-8890}"
KAG_BRIDGE_KEY_FILE="${KAG_BRIDGE_KEY_FILE:-$REPO_ROOT/data/system/kag-bridge-api-key}"
SPG_SERVER_CONTAINER="${SPG_SERVER_CONTAINER:-release-openspg-server}"
SPG_DEPS_CONTAINERS="${SPG_DEPS_CONTAINERS:-release-openspg-mysql release-openspg-neo4j release-openspg-minio}"
BRIDGE_LOG="$REPO_ROOT/data/user/logs/kag-bridge.log"
BRIDGE_PID_FILE="$REPO_ROOT/data/system/kag-bridge.pid"
SPG_URL="http://127.0.0.1:8887"

log() { printf '[kag-stack] %s\n' "$*"; }
die() { printf '[kag-stack] 错误：%s\n' "$*" >&2; exit 1; }

ensure_docker() {
  if docker info >/dev/null 2>&1; then
    return
  fi
  log "Docker 守护进程未运行，尝试启动 OrbStack…"
  open -a OrbStack >/dev/null 2>&1 || die "OrbStack 启动失败"
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && return
    sleep 1
  done
  die "Docker 守护进程 60s 内未就绪"
}

start_container() {
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    die "容器 $name 不存在（本机未创建过 OpenSPG 栈，请先按 design §5.1 部署）"
  fi
  if [ "$(docker inspect "$name" --format '{{.State.Status}}')" = "running" ]; then
    log "容器 $name 已在运行"
  else
    docker start "$name" >/dev/null
    log "容器 $name 已启动"
  fi
}

wait_http() {
  local url="$1" what="$2" tries="${3:-60}"
  for _ in $(seq 1 "$tries"); do
    if curl -s -o /dev/null -m 3 "$url"; then
      log "${what} 就绪（${url}）"
      return 0
    fi
    sleep 2
  done
  die "${what} 未在预期时间内就绪（${url}）"
}

ensure_bridge_key() {
  if [ -s "$KAG_BRIDGE_KEY_FILE" ]; then
    log "复用已有 bridge API key：$KAG_BRIDGE_KEY_FILE"
  else
    mkdir -p "$(dirname "$KAG_BRIDGE_KEY_FILE")"
    openssl rand -hex 32 > "$KAG_BRIDGE_KEY_FILE"
    chmod 600 "$KAG_BRIDGE_KEY_FILE"
    log "已生成 bridge API key：$KAG_BRIDGE_KEY_FILE"
  fi
  KAG_BRIDGE_API_KEY="$(cat "$KAG_BRIDGE_KEY_FILE")"
}

start_bridge() {
  # 停止本脚本此前启动的实例
  if [ -s "$BRIDGE_PID_FILE" ] && kill -0 "$(cat "$BRIDGE_PID_FILE")" 2>/dev/null; then
    log "bridge 已在运行（pid $(cat "$BRIDGE_PID_FILE")），先停止"
    kill "$(cat "$BRIDGE_PID_FILE")" 2>/dev/null || true
  fi
  # 清理占用端口的其它实例（非本脚本启动，无 PID 记录）
  local port_pids
  port_pids="$(lsof -nP -tiTCP:"$KAG_BRIDGE_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$port_pids" ]; then
    log "端口 ${KAG_BRIDGE_PORT} 被占用（pid: ${port_pids}），停止以换用本脚本的 key"
    kill $port_pids 2>/dev/null || true
  fi
  for _ in $(seq 1 10); do
    lsof -nP -tiTCP:"$KAG_BRIDGE_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 1
  done
  lsof -nP -tiTCP:"$KAG_BRIDGE_PORT" -sTCP:LISTEN >/dev/null 2>&1 \
    && die "端口 $KAG_BRIDGE_PORT 仍被占用，无法启动 bridge"

  [ -d "$KAG_DIR" ] || die "KAG 目录不存在：$KAG_DIR"
  [ -f "$KAG_DIR/kag_config.yaml" ] || die "$KAG_DIR 下无 kag_config.yaml"
  [ -x "$KAG_PYTHON" ] || die "KAG venv 解释器不存在：$KAG_PYTHON"
  "$KAG_PYTHON" -c "import kag_bridge" 2>/dev/null \
    || die "kag-bridge 未装入该 venv，请先：uv pip install --python $KAG_PYTHON -e $REPO_ROOT/kag-bridge"

  if [ -z "${LLM_API_KEY:-}" ]; then
    log "警告：未设置 LLM_API_KEY——bridge 可启动，但 kag_solve/向量化调用会因缺少凭据失败"
  fi

  mkdir -p "$(dirname "$BRIDGE_LOG")"
  KAG_PROJECT_DIR="$KAG_DIR" \
  KAG_BRIDGE_TRANSPORT=http \
  KAG_BRIDGE_API_KEY="$KAG_BRIDGE_API_KEY" \
  KAG_BRIDGE_HTTP_HOST="$KAG_BRIDGE_HOST" \
  KAG_BRIDGE_HTTP_PORT="$KAG_BRIDGE_PORT" \
    nohup "$KAG_PYTHON" -m kag_bridge > "$BRIDGE_LOG" 2>&1 &
  echo $! > "$BRIDGE_PID_FILE"
  log "bridge 已启动（pid $(cat "$BRIDGE_PID_FILE")，日志 ${BRIDGE_LOG}）"
}

main() {
  ensure_docker

  for name in $SPG_DEPS_CONTAINERS; do
    start_container "$name"
  done
  start_container "$SPG_SERVER_CONTAINER"
  wait_http "$SPG_URL/public/v1/project?tenantId=1" "OpenSPG server"

  ensure_bridge_key
  start_bridge
  wait_http "http://$KAG_BRIDGE_HOST:$KAG_BRIDGE_PORT/healthz" "kag-bridge"
  kill -0 "$(cat "$BRIDGE_PID_FILE")" 2>/dev/null \
    || die "bridge 启动后即退出，请查看日志：$BRIDGE_LOG"

  cat <<EOF

KAG 全栈已就绪
  OpenSPG server : $SPG_URL          （容器 ${SPG_SERVER_CONTAINER}）
  kag-bridge     : http://$KAG_BRIDGE_HOST:$KAG_BRIDGE_PORT/mcp
  KAG 项目目录   : $KAG_DIR
  bridge key     : $KAG_BRIDGE_KEY_FILE
  bridge 日志    : $BRIDGE_LOG

提示：kag_status 的 llm_configured 只判断配置块存在，不校验 key 有效性。
EOF
}

main "$@"