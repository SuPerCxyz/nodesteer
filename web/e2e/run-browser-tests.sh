#!/bin/bash
# NodeSteer E2E 完整测试 - 使用 agent-browser
set -e

BASE_URL="http://192.168.100.249:8080"
AUTH_PROFILE="${NODESTEER_E2E_AUTH_PROFILE:-nodesteer}"
E2E_USERNAME="${NODESTEER_E2E_USERNAME:-admin}"
: "${NODESTEER_E2E_PASSWORD:?请通过受控环境注入 NODESTEER_E2E_PASSWORD，或使用 agent-browser auth profile}"
PASS=0
FAIL=0
TOTAL=0

run_test() {
  local id="$1" name="$2" func="$3"
  TOTAL=$((TOTAL + 1))
  echo ""
  echo "--- [$id] $name ---"
  if eval "$func" 2>&1; then
    echo "✅ PASS: $id"
    PASS=$((PASS + 1))
  else
    echo "❌ FAIL: $id"
    FAIL=$((FAIL + 1))
  fi
}

# 登录函数
do_login() {
  agent-browser auth login "$AUTH_PROFILE" 2>&1
  sleep 2
}

api_login() {
  printf '{"username":"%s","password":"%s"}' "$E2E_USERNAME" "$NODESTEER_E2E_PASSWORD" |
    curl -s -X POST "$BASE_URL/api/login" -H "Content-Type: application/json" --data-binary @- 2>/dev/null
}

# ========== API 测试 ==========
test_api_health() {
  local r=$(curl -s "$BASE_URL/healthz" 2>/dev/null)
  [ "$r" = "ok" ] && return 0 || return 1
}

test_api_login() {
  local r=$(api_login)
  echo "$r" | grep -q "token" && return 0 || return 1
}

test_api_nodes() {
  local token=$(api_login | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  local r=$(curl -s "$BASE_URL/api/nodes" -H "Authorization: Bearer $token" 2>/dev/null)
  local count=$(echo "$r" | grep -o '"hostname"' | wc -l)
  echo "节点数: $count"
  [ "$count" -ge 1 ] && return 0 || return 1
}

test_api_scripts() {
  local token=$(api_login | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  local r=$(curl -s "$BASE_URL/api/scripts" -H "Authorization: Bearer $token" 2>/dev/null)
  echo "脚本数: $(echo "$r" | grep -o '"name"' | wc -l)"
  return 0
}

test_api_tasks() {
  local token=$(api_login | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  local r=$(curl -s "$BASE_URL/api/tasks" -H "Authorization: Bearer $token" 2>/dev/null)
  echo "任务数: $(echo "$r" | grep -o '"name"' | wc -l)"
  return 0
}

test_api_executions() {
  local token=$(api_login | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  local r=$(curl -s "$BASE_URL/api/executions?limit=10" -H "Authorization: Bearer $token" 2>/dev/null)
  echo "执行数: $(echo "$r" | grep -o '"id"' | wc -l)"
  return 0
}

test_api_settings() {
  local token=$(api_login | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  local r=$(curl -s "$BASE_URL/api/settings" -H "Authorization: Bearer $token" 2>/dev/null)
  echo "$r" | grep -q "heartbeat" && return 0 || return 1
}

test_api_users() {
  local token=$(api_login | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  local r=$(curl -s "$BASE_URL/api/users" -H "Authorization: Bearer $token" 2>/dev/null)
  echo "$r" | grep -q "username" && return 0 || return 1
}

test_api_unauthorized() {
  local r=$(curl -s "$BASE_URL/api/nodes" 2>/dev/null)
  echo "$r" | grep -q "unauthorized" && return 0 || return 1
}

# ========== 前端路由守卫测试 ==========
test_guard() {
  local path="$1"
  agent-browser open "$BASE_URL$path" 2>&1
  sleep 2
  local s=$(agent-browser snapshot -i 2>&1)
  echo "$s" | grep -q "登录" && return 0 || return 1
}

# ========== 登录后页面测试 ==========
test_page_after_login() {
  local path="$1" keyword="$2"
  do_login
  agent-browser open "$BASE_URL$path" 2>&1
  sleep 3
  local s=$(agent-browser snapshot -i 2>&1)
  echo "$s" | grep -q "$keyword" && return 0 || echo "未找到: $keyword" && return 1
}

# ========== 执行测试 ==========
echo "=========================================="
echo "NodeSteer E2E 完整测试"
echo "环境: $BASE_URL"
echo "=========================================="

echo ""
echo "=== API 测试 ==="
run_test "API-01" "健康检查" test_api_health
run_test "API-02" "登录 API" test_api_login
run_test "API-03" "未认证请求 401" test_api_unauthorized
run_test "API-04" "节点 API" test_api_nodes
run_test "API-05" "脚本 API" test_api_scripts
run_test "API-06" "任务 API" test_api_tasks
run_test "API-07" "执行 API" test_api_executions
run_test "API-08" "设置 API" test_api_settings
run_test "API-09" "用户 API" test_api_users

echo ""
echo "=== P01 登录页 ==="
run_test "P01-01" "登录页表单" "test_guard /sign-in"

echo ""
echo "=== 前端路由守卫 ==="
for path in / /nodes /scripts /tasks /schedules /applications /artifacts /executions /audit /users /settings /transfers; do
  name=$(echo $path | sed 's/\//-/g' | sed 's/^-//')
  [ -z "$name" ] && name="dashboard"
  run_test "GUARD-$name" "路由守卫 $path" "test_guard $path"
done

echo ""
echo "=== 登录后页面测试 ==="
run_test "P03-01" "仪表盘" "test_page_after_login / 仪表盘"
run_test "P04-01" "节点列表" "test_page_after_login /nodes 节点"
run_test "P07-01" "脚本列表" "test_page_after_login /scripts 脚本"
run_test "P09-01" "任务列表" "test_page_after_login /tasks 任务"
run_test "P12-01" "调度列表" "test_page_after_login /schedules 调度"
run_test "P14-01" "托管应用列表" "test_page_after_login /applications 托管应用"
run_test "P16-01" "发布包列表" "test_page_after_login /artifacts 发布包"
run_test "P17-01" "执行列表" "test_page_after_login /executions 执行"
run_test "P19-01" "审计列表" "test_page_after_login /audit 审计"
run_test "P20-01" "用户列表" "test_page_after_login /users 用户"
run_test "P21-01" "设置页面" "test_page_after_login /settings 设置"
run_test "P22-01" "传输页面" "test_page_after_login /transfers 文件传输"

echo ""
echo "=========================================="
echo "测试结果汇总"
echo "=========================================="
echo "总计: $TOTAL"
echo "通过: $PASS"
echo "失败: $FAIL"
echo "=========================================="

if [ $FAIL -eq 0 ]; then
  echo "✅ 全部测试通过"
  exit 0
else
  echo "❌ 有 $FAIL 个测试失败"
  exit 1
fi
