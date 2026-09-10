#!/bin/bash
# Cadentra E2E 测试运行脚本
# 用法: ./run-tests.sh [环境] [测试模块]
# 环境: sim (模拟环境, 默认) | real (真实环境)
# 测试模块: all (默认) | login | nodes | scripts | tasks | schedules | applications | artifacts | executions | rbac | e2e

set -e

ENV="${1:-sim}"
MODULE="${2:-all}"

case "$ENV" in
  sim)
    BASE_URL="http://192.168.100.249:8080"
    ;;
  real)
    BASE_URL="http://192.168.100.116:8080"
    ;;
  *)
    echo "用法: $0 [sim|real] [all|login|nodes|...]"
    exit 1
    ;;
esac

export BASE_URL

cd "$(dirname "$0")"

echo "=========================================="
echo "Cadentra E2E 测试"
echo "环境: $ENV ($BASE_URL)"
echo "模块: $MODULE"
echo "=========================================="

case "$MODULE" in
  all)
    npx playwright test --config e2e/playwright.config.ts
    ;;
  login)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p01-login.spec.ts
    ;;
  nodes)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p04-nodes.spec.ts
    ;;
  scripts)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p07-scripts.spec.ts
    ;;
  tasks)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p09-tasks.spec.ts
    ;;
  schedules)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p12-schedules.spec.ts
    ;;
  applications)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p14-applications.spec.ts
    ;;
  artifacts)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p16-artifacts.spec.ts
    ;;
  executions)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/p17-executions.spec.ts
    ;;
  rbac)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/rbac.spec.ts
    ;;
  e2e)
    npx playwright test --config e2e/playwright.config.ts e2e/tests/e2e-flows.spec.ts
    ;;
  *)
    echo "未知模块: $MODULE"
    echo "可用模块: all login nodes scripts tasks schedules applications artifacts executions rbac e2e"
    exit 1
    ;;
esac

echo ""
echo "=========================================="
echo "测试完成"
echo "报告: npx playwright show-report e2e/playwright-report"
echo "=========================================="
