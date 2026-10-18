#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build control-inventory.json for the NodeSteer repair control-level coverage audit.

Inputs (read-only):
  - .e2e/runs/20260930-101945/repair/feature-inventory.json   (504 cases)
  - raw/*.json                                                (live captures 2026-10-17)
Outputs:
  - ../control-inventory.json
"""
import json, os, re
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.dirname(BASE)
REPO = '/home/superc/code/nodesteer'

# ---------------------------------------------------------------- case index
inv = json.load(open(f'{REPO}/.e2e/runs/20260930-101945/repair/feature-inventory.json', encoding='utf-8'))
CASES = {}
for c in inv['cases']:
    text = ' | '.join(filter(None, [
        str(c.get('title', '')), str(c.get('actual', '')),
        ' '.join(str(d.get('description', '')) if isinstance(d, dict) else str(d)
                 for d in (c.get('covered_dimensions') or [])),
    ]))
    CASES[c['case_id']] = {
        'case_id': c['case_id'], 'owner': c.get('owner'), 'status': c.get('status'),
        'priority': c.get('priority'), 'title': c.get('title', ''), 'text': text,
    }

def have(cid):
    return cid in CASES

def status_of(cids):
    return {c: CASES[c]['status'] for c in cids if have(c)}

# ---------------------------------------------------------------- shell set
NAV_NAMES = {'仪表盘', '任务', '调度', '执行', '文件传输', '节点', '分组', '脚本', '应用', '审计', '用户', '设置'}
SHELL_NAMES = NAV_NAMES | {
    '语言', '切换主题', '个人资料', '切换侧边栏', 'Toggle Sidebar', 'Skip to Main',
    'NodeSteer Linux 节点自动化控制台', 'qa-repair-20260930-101945-admin 管理员',
    '中文', 'English', '浅色', '深色', '系统', '退出登录',
}

# ---------------------------------------------------------------- pages
# base: for menu/dialog captures, the underlying page id used for delta filtering
PAGES = [
    dict(id='global-shell', route='(全局壳)', title='侧边栏 + 顶栏 + 语言/主题/资料菜单',
         files=['dashboard', 'menu-language', 'menu-theme', 'menu-profile'], base=None, state='ok',
         evidence=['evidence/page-dashboard.png'],
         note='侧边栏 11 导航项 + 品牌链接 + 折叠按钮 + 语言/主题/资料菜单（含菜单项）'),
    dict(id='sign-in', route='/sign-in', title='登录', files=['sign-in'], base=None, state='ok',
         evidence=['evidence/page-sign-in.png'], note='未登录会话（独立 session）捕获'),
    dict(id='dashboard', route='/', title='仪表盘', files=['dashboard'], base=None, state='ok',
         evidence=['evidence/page-dashboard.png'], note=''),
    dict(id='nodes-list', route='/agents', title='节点列表', files=['nodes-list'], base=None, state='ok',
         evidence=['evidence/page-nodes-list.png'], note='6 节点；含行选择列'),
    dict(id='nodes-bulk', route='/agents（选中 1 行后）', title='节点列表 · 批量工具条', files=['sel-row-nodes'],
         base='nodes-list', state='ok', evidence=[], note='勾选第 1 行后浮出批量工具条'),
    dict(id='node-detail', route='/agents/{id}', title='节点详情', files=['node-detail'], base=None, state='ok',
         evidence=['evidence/page-node-detail.png'], note='含执行历史 100 行'),
    dict(id='node-detail-addtag', route='/agents/{id}（添加标签）', title='节点详情 · 添加标签表单',
         files=['form-addtag-node'], base='node-detail', state='ok', evidence=[],
         note='标签 key/value 表单常驻渲染（非点击后新增），控件已并入 node-detail；此处无增量控件'),
    dict(id='dialog-add-node', route='/agents（添加节点）', title='添加节点对话框', files=['dialog-add-node'],
         base='nodes-list', state='ok', evidence=[], note='Esc 可关闭'),
    dict(id='menu-view', route='（各列表 · 视图）', title='视图（列可见性）菜单', files=['menu-view-nodes'],
         base='nodes-list', state='ok', evidence=[], note='以节点列表为例；其它列表复用同一组件'),
    dict(id='menu-row-nodes', route='/agents（行菜单）', title='节点行操作菜单', files=['menu-row-nodes'],
         base='nodes-list', state='ok', evidence=[], note=''),
    dict(id='groups-list', route='/groups', title='分组列表', files=['groups-list'], base=None, state='empty',
         evidence=['evidence/page-groups-list.png'], note='当前 0 分组 → 仅“暂无数据”空态，无工具栏'),
    dict(id='groups-new', route='/groups/new', title='新建分组', files=['groups-new'], base=None, state='ok',
         evidence=['evidence/page-groups-new.png'], note=''),
    dict(id='scripts-list', route='/scripts', title='脚本列表', files=['scripts-list'], base=None, state='ok',
         evidence=['evidence/page-scripts-list.png'], note='2 脚本'),
    dict(id='scripts-new', route='/scripts/new', title='新建脚本', files=['scripts-new'], base=None, state='ok',
         evidence=['evidence/page-scripts-new.png'], note=''),
    dict(id='script-detail', route='/scripts/{id}', title='编辑脚本', files=['script-detail'], base=None, state='ok',
         evidence=['evidence/page-script-detail.png'], note=''),
    dict(id='menu-row-scripts', route='/scripts（行菜单）', title='脚本行操作菜单', files=['menu-row-scripts'],
         base='scripts-list', state='ok', evidence=[], note=''),
    dict(id='tasks-list', route='/tasks', title='任务列表', files=['tasks-list'], base=None, state='ok',
         evidence=['evidence/page-tasks-list.png'], note='10/16 任务；行名链接 + 立即运行'),
    dict(id='schedules-view', route='/tasks?view=schedules', title='任务页 · 调度视图', files=['schedules-view'],
         base=None, state='empty', evidence=['evidence/page-schedules-view.png'],
         note='当前 0 调度 → “暂无数据”空态，无工具栏；/schedules 与 ?view=schedules 均可达'),
    dict(id='tasks-new', route='/tasks/new', title='新建任务', files=['tasks-new'], base=None, state='ok',
         evidence=['evidence/page-tasks-new.png'], note=''),
    dict(id='task-detail', route='/tasks/{id}', title='任务详情', files=['task-detail'], base=None, state='ok',
         evidence=[], note='捕获时有任务；并发 QA 清理后当前任务列表为空'),
    dict(id='task-detail-edit', route='/tasks/{id}/edit', title='编辑任务', files=['task-detail-edit'], base=None,
         state='fixture-missing', evidence=[], note='捕获时任务已被并发清理 → 页面为“任务不存在或已被删除”错误态'),
    dict(id='task-detail-run', route='/tasks/{id}/run', title='立即运行', files=['task-detail-run'], base=None,
         state='fixture-missing', evidence=[], note='捕获时任务已被并发清理 → 页面为错误态'),
    dict(id='schedules-new', route='/schedules/new', title='新建调度', files=['schedules-new'], base=None, state='ok',
         evidence=['evidence/page-schedules-new.png'], note=''),
    dict(id='artifacts-list', route='/artifacts', title='发布包列表', files=['artifacts-list'], base=None, state='ok',
         evidence=['evidence/page-artifacts-list.png'], note='1 发布包'),
    dict(id='artifacts-new', route='/artifacts/new', title='上传发布包', files=['artifacts-new'], base=None, state='ok',
         evidence=['evidence/page-artifacts-new.png'], note=''),
    dict(id='menu-row-artifacts', route='/artifacts（行菜单）', title='发布包行操作菜单', files=['menu-row-artifacts'],
         base='artifacts-list', state='ok', evidence=[], note=''),
    dict(id='applications-list', route='/applications', title='托管应用列表', files=['applications-list'], base=None,
         state='ok', evidence=['evidence/page-applications-list.png'], note='1 应用'),
    dict(id='applications-new', route='/applications/new', title='新建托管应用', files=['applications-new'], base=None,
         state='ok', evidence=['evidence/page-applications-new.png'], note=''),
    dict(id='application-detail', route='/applications/{id}', title='编辑托管应用', files=['application-detail'],
         base=None, state='ok', evidence=['evidence/page-application-detail.png'], note=''),
    dict(id='menu-row-applications', route='/applications（行菜单）', title='应用行操作菜单',
         files=['menu-row-applications'], base='applications-list', state='ok', evidence=[], note=''),
    dict(id='executions-list', route='/executions', title='执行列表', files=['executions-list'], base=None, state='ok',
         evidence=['evidence/page-executions-list.png'], note='1545 执行；任务/节点/查看详情三类链接'),
    dict(id='execution-detail', route='/executions/{id}', title='执行详情',
         files=['execution-detail-real', 'execution-detail'], base=None, state='ok',
         evidence=['evidence/page-execution-detail-real.png'], note='以真实任务执行为准；“未知任务”执行无任务回链'),
    dict(id='transfers-list', route='/transfers', title='文件传输', files=['transfers-list'], base=None, state='ok',
         evidence=['evidence/page-transfers-list.png'], note='含页内新建传输表单'),
    dict(id='menu-row-transfers', route='/transfers（行菜单）', title='传输行操作菜单', files=['menu-row-transfers'],
         base='transfers-list', state='ok', evidence=[], note=''),
    dict(id='audit-list', route='/audit', title='审计日志', files=['audit-list'], base=None, state='ok',
         evidence=['evidence/page-audit-list.png'], note=''),
    dict(id='users-list', route='/users', title='用户列表', files=['users-list', 'combo-user-role2'], base=None,
         state='ok', evidence=['evidence/page-users-list.png'], note='8 用户；行内角色下拉'),
    dict(id='dialog-new-user', route='/users（新建用户）', title='新建用户对话框', files=['dialog-new-user'],
         base='users-list', state='ok', evidence=[], note=''),
    dict(id='settings', route='/settings', title='设置', files=['settings'], base=None, state='ok',
         evidence=['evidence/page-settings.png'], note=''),
    dict(id='route-404', route='/invalid-e2e-route', title='页面不存在', files=['route-404'], base=None, state='ok',
         evidence=['evidence/page-route-404.png'], note=''),
    dict(id='route-error', route='/{resource}/nonexistent-id', title='不存在资源错误态',
         files=['agent-nonexistent', 'task-nonexistent', 'group-nonexistent', 'schedule-nonexistent',
                'execution-nonexistent', 'script-nonexistent', 'application-nonexistent'],
         base=None, state='ok', evidence=['evidence/page-agent-nonexistent.png'],
         note='7 类资源均为“不存在或已被删除 + 重试 + 返回”错误态'),
]

# ---------------------------------------------------------------- coverage rules
R = [
    # global shell
    ('*', lambda n, p: n in NAV_NAMES, ['P02-01', 'G02-08', 'G02-09', 'ROUTE-01'], 'full', 'Sidebar 导航项'),
    ('*', lambda n, p: n == '语言', ['G02-01', 'P01-06', 'P02-02'], 'full', '语言菜单'),
    ('*', lambda n, p: n == '切换主题', ['G02-02', 'P02-02'], 'full', '主题菜单'),
    ('*', lambda n, p: n == '个人资料', ['G02-03', 'G02-04', 'P02-02'], 'full', '用户菜单'),
    ('*', lambda n, p: n in ('切换侧边栏', 'Toggle Sidebar'), ['G02-06', 'G02-07', 'P02-03'], 'full', '侧边栏折叠'),
    ('*', lambda n, p: n == 'Skip to Main', ['G04-04', 'X19'], 'partial', '键盘可达性仅 PARTIAL'),
    ('*', lambda n, p: n.startswith('qa-repair-20260930-101945-admin'), ['P02-02'], 'full', '用户菜单触发器'),
    ('*', lambda n, p: n in ('中文', 'English'), ['G02-01', 'P01-06'], 'full', '语言菜单项'),
    ('*', lambda n, p: n in ('浅色', '深色', '系统'), ['G02-02'], 'full', '主题菜单项'),
    ('*', lambda n, p: n == '退出登录', ['G02-04', 'G02-05'], 'full', '退出登录确认/取消'),
    ('*', lambda n, p: n in ('← 返回', '返回'), ['P05B-11', 'X07', 'P02-04'], 'full', '返回'),
    ('*', lambda n, p: n == '重试', ['G03-09', 'P04-09', 'X07'], 'full', 'Error/Retry'),
    ('*', lambda n, p: n == '全选所有行', ['X14', 'X19'], 'partial', 'X14 FAIL 选择漂移；X19 PARTIAL'),
    ('*', lambda n, p: n == '选择该行', ['X14', 'X19'], 'partial', 'X14 FAIL 选择漂移；X19 PARTIAL'),
    ('*', lambda n, p: n == '清除选择', ['X19', 'X14'], 'partial', '批量工具条清除'),
    ('*', lambda n, p: n.startswith('升级 Agent'), ['X01', 'X08'], 'full', '批量升级（本审计未点击）'),
    ('*', lambda n, p: n.startswith('设为维护'), ['X01', 'X08'], 'full', '批量暂停（本审计未点击）'),
    ('*', lambda n, p: n.startswith('设为在线'), ['X01', 'X08'], 'full', '批量恢复（本审计未点击）'),
    # sign-in
    ('sign-in', lambda n, p: n == '用户名', ['P01-01', 'P01-02', 'P01-03'], 'full', ''),
    ('sign-in', lambda n, p: n == '密码', ['P01-01', 'P01-02', 'P01-03'], 'full', ''),
    ('sign-in', lambda n, p: n == '显示密码', ['P01-05'], 'full', ''),
    ('sign-in', lambda n, p: n == '登录', ['P01-01', 'P01-04'], 'full', '登录提交'),
    # dashboard
    ('dashboard', lambda n, p: n == '查看全部', ['P03-02', 'P03-03', 'P03-04', 'P03-05', 'P03-06', 'P03-07'],
     'full', 'View All → 列表（已实测 4 个目标）'),
    ('dashboard', lambda n, p: n.endswith('健康') or n.endswith('异常') or n.endswith('未知') or n.endswith('同步'),
     ['P03-04', 'P03-09'], 'full', '应用/同步状态链接'),
    ('dashboard', lambda n, p: n.startswith('qa-') and n.endswith('健康'), ['P03-04'], 'full', '应用健康链接'),
    ('dashboard', lambda n, p: n.startswith('qa-') or n == '未知任务', ['P03-06', 'P03-07'], 'full',
     '最近执行/调度行链接 → 执行详情'),
    ('global-shell', lambda n, p: n.startswith('qa-') or n == '未知任务', ['P03-06'], 'full', '仪表盘执行行链接（随 shell 捕获）'),
    ('global-shell', lambda n, p: n.endswith('健康'), ['P03-04'], 'full', '仪表盘应用链接（随 shell 捕获）'),
    ('global-shell', lambda n, p: n == '查看全部', ['P03-02', 'P03-03', 'P03-04', 'P03-05', 'P03-06', 'P03-07'],
     'full', '仪表盘 View All（随 shell 捕获）'),
    ('global-shell', lambda n, p: n == 'NodeSteer Linux 节点自动化控制台', ['P02-01', 'G02-08'], 'full', '品牌链接 → 仪表盘'),
    # menu-view / row menus
    ('menu-view', lambda n, p: n == '切换列', ['TABLE-NODES-COLUMNS', 'G03-05'], 'full', '视图菜单标题'),
    ('menu-view', lambda n, p: n in ('状态', '主机名', '系统', '节点地址', '架构', 'Agent 版本', '部署模式', '最后在线'),
     ['TABLE-NODES-COLUMNS', 'G03-05'], 'full', '列可见性开关（已实测隐藏/恢复/持久化）'),
    ('menu-row-nodes', lambda n, p: n in ('维护', '禁用', '设为在线', '撤销凭证', '删除'),
     ['P04-04', 'P04-05', 'X02', 'G04-02'], 'full', '节点行菜单项'),
    # nodes
    ('nodes-list', lambda n, p: n == '添加节点', ['P05A-01', 'P04-07'], 'full', '对话框触发'),
    ('nodes-list', lambda n, p: n.startswith('搜索'), ['P04-08', 'TABLE-NODES-SEARCH', 'G03-01', 'G03-02', 'G03-03', 'G03-04'], 'full', ''),
    ('nodes-list', lambda n, p: n == '操作', ['P04-04', 'P04-05', 'G04-02'], 'full', '行菜单'),
    ('nodes-list', lambda n, p: n.startswith('qa-'), ['P04-03'], 'full', '行名链接 → 节点详情'),
    ('node-detail', lambda n, p: n == '添加标签', ['P05B-06'], 'full', ''),
    ('node-detail', lambda n, p: n in ('key', 'value'), ['P05B-06'], 'full', '标签 key/value 输入（表单常驻）'),
    ('script-detail', lambda n, p: n in ('KEY', 'VALUE'), ['P07_P08-06'], 'full', '环境变量行'),
    ('script-detail', lambda n, p: n == 'Shell', ['P07_P08-03'], 'full', '解释器（编辑态）'),
    ('menu-row-transfers', lambda n, p: n.startswith('/var/lib'), ['FL-13'], 'full', '路径输入（随行菜单捕获）'),
    ('node-detail', lambda n, p: n == '移除', ['P05B-06'], 'none', '标签移除入口：基线记录“无删除入口”，需确认修复版新增行为'),
    ('node-detail', lambda n, p: n == '查看详情', ['P05B-10', 'P05B-07', 'P17P18-06'], 'full', '执行回链'),
    ('node-detail-addtag', lambda n, p: n in ('key', 'value', '保存', '取消'), ['P05B-06'], 'full', '标签 key/value'),
    ('dialog-add-node', lambda n, p: n in ('节点名称', '节点地址（IP 或域名）', 'Hub 地址'),
     ['P05A-02', 'P05A-03', 'P05A-04', 'P05A-05'], 'full', ''),
    ('dialog-add-node', lambda n, p: n == '生成安装命令', ['P05A-06', 'P05A-07', 'P05A-12'], 'full', '生成命令（本审计未提交）'),
    ('dialog-add-node', lambda n, p: n in ('Close', '关闭'), ['P05A-01', 'P05A-13'], 'full', ''),
    ('dialog-add-node', lambda n, p: n in ('Agent', 'Native', 'docker run', 'Docker', 'Compose', 'docker compose'),
     ['P05A-08', 'P05A-09'], 'full', '三种纳管命令 Tab'),
    # groups
    ('groups-list', lambda n, p: n == '新建分组', ['P06-02', 'P06-03', 'P06-09'], 'full', ''),
    ('groups-new', lambda n, p: n in ('名称', '描述'), ['P06-02', 'P06-03'], 'full', ''),
    ('groups-new', lambda n, p: n == '静态', ['P06-02', 'P06-05'], 'full', '静态/标签类型'),
    ('groups-new', lambda n, p: n in ('保存', '取消'), ['P06-02', 'P06-09', 'X08'], 'full', '（本审计未提交）'),
    # scripts
    ('scripts-list', lambda n, p: n == '新建脚本', ['P07_P08-02'], 'full', ''),
    ('scripts-list', lambda n, p: n.startswith('搜索'), ['TABLE-SCRIPTS-SEARCH'], 'full', ''),
    ('scripts-list', lambda n, p: n == '操作', ['P07_P08-09', 'P07_P08-10'], 'full', '行菜单'),
    ('scripts-list', lambda n, p: n.startswith('qa-web-b05'), ['P07_P08-08'], 'full', '行名链接 → 编辑脚本'),
    ('scripts-new', lambda n, p: n in ('Shell', 'Bash', 'Python'), ['P07_P08-03'], 'full', '解释器'),
    ('scripts-new', lambda n, p: n in ('名称', '描述', '超时', '工作目录', '运行用户', '已启用', '内容', 'textarea'),
     ['P07_P08-04'], 'full', '脚本表单字段'),
    ('scripts-new', lambda n, p: n in ('string', 'number', 'text', '参数名称', '默认值'),
     ['P07_P08-05'], 'full', '参数行字段'),
    ('script-detail', lambda n, p: n in ('名称', '描述', '超时', '工作目录', '运行用户', '已启用', '内容', 'textarea'),
     ['P07_P08-08'], 'full', '脚本编辑字段'),
    ('script-detail', lambda n, p: n in ('string', 'number', 'text', '参数名称', '默认值'),
     ['P07_P08-05', 'P07_P08-08'], 'full', '参数行字段'),
    ('script-detail', lambda n, p: n in ('添加参数', '添加'), ['P07_P08-05', 'P07_P08-06'], 'full', '新增参数/环境变量'),
    ('script-detail', lambda n, p: n == '移除', ['P07_P08-05', 'P07_P08-06'], 'full', '删除参数/环境变量行'),
    ('script-detail', lambda n, p: n.startswith('r') and len(n) > 5, ['P07_P08-08'], 'full', 'Revision 历史展开'),
    ('scripts-new', lambda n, p: n in ('添加参数', '默认值', '参数名称'), ['P07_P08-05'], 'full', ''),
    ('scripts-new', lambda n, p: n in ('KEY', 'VALUE', '添加'), ['P07_P08-06'], 'full', ''),
    ('scripts-new', lambda n, p: n == '保存', ['P07_P08-07', 'G04-01'], 'full', '（本审计未提交）'),
    ('scripts-new', lambda n, p: n == '取消', ['P07_P08-02', 'X08'], 'full', ''),
    ('script-detail', lambda n, p: n == '保存', ['P07_P08-08'], 'full', '（本审计未提交）'),
    ('script-detail', lambda n, p: n == '取消', ['X08'], 'full', ''),
    ('script-detail', lambda n, p: n == '删除', ['P07_P08-10', 'G04-02'], 'full', '确认框'),
    ('menu-row-scripts', lambda n, p: n in ('编辑', '禁用', '启用', '克隆', '删除'),
     ['P07_P08-09', 'P07_P08-10', 'P09P11-03'], 'full', ''),
    # tasks
    ('tasks-list', lambda n, p: n == '新建任务', ['P09P11-02'], 'full', ''),
    ('tasks-list', lambda n, p: n.startswith('搜索'), ['TABLE-TASKS-SEARCH'], 'full', ''),
    ('tasks-list', lambda n, p: n == '操作', ['P09P11-03', 'P09P11-04'], 'full', '行菜单'),
    ('tasks-list', lambda n, p: n == '立即运行', ['P11-01', 'P11-02', 'P11-03', 'FL-05'], 'full', '（本审计未点击）'),
    ('tasks-list', lambda n, p: n.startswith('qa-'), ['P09P11-02', 'P09P11-18'], 'full', '行名链接 → 任务详情'),
    ('schedules-view', lambda n, p: n == '新建调度', ['P12P13-02', 'X05'], 'full', ''),
    ('tasks-new', lambda n, p: n in ('命令', '脚本', '应用', '部署'), ['P09P11-05', 'P09P11-06', 'P09P11-07', 'P09P11-08'], 'full', '任务类型'),
    ('tasks-new', lambda n, p: n.startswith('选择'), ['P09P11-06', 'P09P11-07', 'P09P11-09'], 'full', '选择器'),
    ('tasks-new', lambda n, p: n in ('设置条件', '设置远程条件'), ['P09P11-15', 'P09P11-16', 'P09P11-17', 'X11'], 'full', '条件编辑'),
    ('tasks-new', lambda n, p: n == '添加参数', ['P09P11-14'], 'full', ''),
    ('tasks-new', lambda n, p: n in ('名称', '超时（秒）', '重试次数', '运行用户', 'textarea', 'text', 'number'),
     ['P09P11-13'], 'full', '任务表单字段'),
    ('tasks-new', lambda n, p: n in ('参数名称', '默认值', 'value', 'string'), ['P09P11-14'], 'full', '参数行字段'),
    ('tasks-new', lambda n, p: n in ('cpu_usage', 'online', '==', '>', 'string'),
     ['P09P11-15', 'P09P11-16'], 'full', '条件编辑（metric/operator/value）'),
    ('tasks-new', lambda n, p: n in ('需 Hub 在线', '允许离线'), ['P09P11-13', 'P12P13-07'], 'full', ''),
    ('tasks-new', lambda n, p: n == '保存', ['P09P11-05', 'X09', 'G04-01'], 'full', '（本审计未提交）'),
    ('tasks-new', lambda n, p: n == '取消', ['X08', 'X09'], 'full', ''),
    ('task-detail', lambda n, p: n == '立即运行', ['P11-01', 'P11-02', 'FL-05'], 'full', '（本审计未点击）'),
    ('task-detail', lambda n, p: n == '编辑', ['P09P11-18', 'X09'], 'full', ''),
    ('task-detail', lambda n, p: n == '新建调度', ['X05', 'P12P13-11'], 'full', ''),
    ('task-detail', lambda n, p: n == '查看详情', ['P09P11-20', 'P09P11-21', 'X07'], 'full', ''),
    ('task-detail-edit', lambda n, p: True, ['ROUTE-16', 'X07'], 'fixture', '需任务夹具（当前并发清理）'),
    ('task-detail-run', lambda n, p: True, ['ROUTE-17', 'X07', 'P11-01'], 'fixture', '需任务夹具（当前并发清理）'),
    # schedules
    ('schedules-new', lambda n, p: n in ('Cron', 'Cron 表达式'), ['P12P13-03', 'X04'], 'full', ''),
    ('schedules-new', lambda n, p: n in ('常用预设',), ['X04'], 'full', ''),
    ('schedules-new', lambda n, p: n in ('分', '时', '日', '月', '周'), ['X04'], 'full', 'Cron 五字段'),
    ('schedules-new', lambda n, p: n.startswith('选择任务'), ['X05', 'P12P13-02'], 'full', ''),
    ('schedules-new', lambda n, p: n in ('时区',), ['P12P13-05', 'X10'], 'full', '时区'),
    ('schedules-new', lambda n, p: n in ('Agent', 'Hub'), ['P12P13-06'], 'full', '执行方 Owner'),
    ('schedules-new', lambda n, p: n in ('允许离线', '需 Hub 在线'), ['P12P13-07'], 'full', '离线策略'),
    ('schedules-new', lambda n, p: n in ('补跑一次', '跳过'), ['P12P13-08', 'E2E-06'], 'full', 'Misfire 策略'),
    ('schedules-new', lambda n, p: n == '保存', ['P12P13-01', 'G04-01'], 'full', '（本审计未提交）'),
    ('schedules-new', lambda n, p: n == '取消', ['P12P13-12'], 'full', ''),
    # artifacts
    ('artifacts-list', lambda n, p: n == '上传', ['P16-01', 'P16-02'], 'full', ''),
    ('artifacts-list', lambda n, p: n.startswith('搜索'), ['TABLE-ARTIFACTS-SEARCH'], 'full', ''),
    ('artifacts-list', lambda n, p: n == '操作', ['X06', 'P16-02'], 'full', '行菜单'),
    ('artifacts-list', lambda n, p: n.startswith('qa-'), ['FL-08'], 'full', 'Usage 列 → 应用详情'),
    ('artifacts-new', lambda n, p: n in ('名称', '版本', '架构', 'amd64', 'arm64', '文件') or n.startswith('架构'),
     ['P16-02', 'P16-03'], 'full', '上传表单字段'),
    ('artifacts-new', lambda n, p: n.startswith('input:'), ['P16-02', 'P16-03'], 'full', '上传表单字段（无标签输入）'),
    ('artifacts-new', lambda n, p: n == '上传', ['P16-02', 'P16-03', 'G04-01'], 'full', '（本审计未提交）'),
    ('artifacts-new', lambda n, p: n == '取消', ['X06'], 'full', ''),
    ('menu-row-artifacts', lambda n, p: n in ('下载', '创建应用', '删除'), ['X06', 'FL-08'], 'full', ''),
    # applications
    ('applications-list', lambda n, p: n == '新建托管应用', ['P14P15-02'], 'full', ''),
    ('applications-list', lambda n, p: n.startswith('搜索'), ['TABLE-APPLICATIONS-SEARCH'], 'full', ''),
    ('applications-list', lambda n, p: n == '操作', ['P14P15-16'], 'full', '行菜单'),
    ('applications-list', lambda n, p: n.startswith('qa-'), ['P14P15-07'], 'full', '行名链接 → 编辑应用'),
    ('applications-new', lambda n, p: n in ('名称', '版本', '描述', '二进制路径', '配置路径', 'Unit', '配置内容'),
     ['P14P15-02'], 'full', ''),
    ('applications-new', lambda n, p: n in ('上传新发布包', '发布包管理'), ['X06', 'FL-08'], 'full', '发布包入口'),
    ('applications-new', lambda n, p: n in ('systemd', 'deploy', '--port 8080'), ['P14P15-04', 'P14P15-11'], 'full',
     '健康检查类型/操作/参数值'),
    ('applications-new', lambda n, p: n.startswith('选择'), ['X06', 'P14P15-02'], 'full', ''),
    ('applications-new', lambda n, p: n in ('健康检查类型', '健康检查目标', '检查超时（秒）', '检查次数', '检查间隔（秒）'),
     ['P14P15-04'], 'full', ''),
    ('applications-new', lambda n, p: n in ('KEY', 'VALUE', '添加', '添加参数'), ['P14P15-03'], 'full', ''),
    ('applications-new', lambda n, p: n == '保存', ['P14P15-06', 'G04-01'], 'full', '（本审计未提交）'),
    ('applications-new', lambda n, p: n == '取消', ['P14P15-01', 'X08'], 'full', ''),
    ('application-detail', lambda n, p: n in ('systemd', 'deploy', '--port 8080'), ['P14P15-04', 'P14P15-11'], 'full',
     '健康检查类型/操作/参数值'),
    ('application-detail', lambda n, p: n.startswith('r') and len(n) < 6, ['P14P15-07'], 'full', '版本历史 r1/r2 展开'),
    ('application-detail', lambda n, p: n == '部署', ['P14P15-10', 'P14P15-11', 'FL-10'], 'full', '（本审计未点击）'),
    ('application-detail', lambda n, p: n == '保存', ['P14P15-07', 'G04-01'], 'full', '（本审计未提交）'),
    ('application-detail', lambda n, p: n == '取消', ['X08'], 'full', ''),
    ('application-detail', lambda n, p: n == '发布包管理', ['FL-08', 'X06'], 'full', '→ /artifacts（已实测）'),
    ('application-detail', lambda n, p: n == '上传新发布包', ['X06'], 'full', '（本审计未提交）'),
    ('application-detail', lambda n, p: n.startswith('qa-'), ['P14P15-08', 'FL-09'], 'full', '分配节点勾选'),
    ('application-detail', lambda n, p: n in ('名称', '版本', '描述', '二进制路径', '配置路径', 'Unit', '配置内容',
                                              '健康检查类型', '健康检查目标', '检查超时（秒）', '检查次数', '检查间隔（秒）',
                                              'KEY', 'VALUE', '添加', '添加参数', '默认值'),
     ['P14P15-02', 'P14P15-03', 'P14P15-04'], 'full', ''),
    ('menu-row-applications', lambda n, p: n in ('编辑', '删除'), ['P14P15-16'], 'full', ''),
    # executions
    ('executions-list', lambda n, p: n.startswith('搜索'), ['P17P18-02', 'TABLE-EXECUTIONS-SEARCH'], 'full', ''),
    ('executions-list', lambda n, p: n == '状态', ['P17P18-03'], 'full', ''),
    ('executions-list', lambda n, p: n == '触发方式', ['P17P18-04'], 'partial', 'trigger 过滤仅 PARTIAL'),
    ('executions-list', lambda n, p: n == '查看详情', ['P17P18-06', 'X14'], 'full', ''),
    ('executions-list', lambda n, p: n.startswith('qa-'), ['P17P18-06'], 'full', '任务/节点回链'),
    ('execution-detail', lambda n, p: n == '打开日志', ['P17P18-08'], 'partial',
     '实为 scrollIntoView 到页内日志区；P17P18-08 只验证分区结构，按钮滚动效果无用例'),
    ('execution-detail', lambda n, p: n == '复制日志', ['P17P18-10'], 'full', ''),
    ('execution-detail', lambda n, p: n == '换行', ['P17P18-10'], 'full', ''),
    ('execution-detail', lambda n, p: n == '跟随', ['P17P18-10', 'X15'], 'full', ''),
    ('execution-detail', lambda n, p: n in ('全部流', 'stdout', 'stderr'), ['P17P18-09', 'X15'], 'full', ''),
    ('execution-detail', lambda n, p: n.startswith('搜索'), ['P17P18-09', 'X15'], 'full', ''),
    ('execution-detail', lambda n, p: n.startswith('qa-') or n.startswith('未知'), ['P17P18-06', 'P05B-10'], 'full',
     '任务/节点回链（真实任务执行有链接）'),
    # transfers
    ('transfers-list', lambda n, p: n.startswith('选择'), ['E2E-14', 'FL-13'], 'full', ''),
    ('transfers-list', lambda n, p: n == '添加目标', ['X18', 'E2E-14'], 'full', ''),
    ('transfers-list', lambda n, p: n == '开始传输', ['FL-13', 'E2E-14', 'G04-01'], 'full', '（本审计未提交）'),
    ('transfers-list', lambda n, p: n.startswith('搜索'), ['TABLE-TRANSFERS-SEARCH'], 'full', ''),
    ('transfers-list', lambda n, p: n in ('源文件路径', '目标文件路径') or n.startswith('/var/lib'), ['FL-13', 'E2E-14'], 'full',
     '源/目标路径输入'),
    ('transfers-list', lambda n, p: n == '操作', ['E2E-14', 'X18'], 'full', '行菜单'),
    ('transfers-list', lambda n, p: n == '/var/lib/app/config.yaml', ['FL-13'], 'full', '路径输入（随行菜单捕获）'),
    ('transfers-list', lambda n, p: n == '/var/lib/app/config.yaml', ['P16-02' if False else 'FL-13'], 'full', '源/目标路径输入'),
    ('menu-row-transfers', lambda n, p: n in ('重试', '取消传输'), ['E2E-14', 'X18'], 'full', ''),
    # audit / users / settings
    ('audit-list', lambda n, p: n.startswith('搜索'), ['TABLE-AUDIT-SEARCH', 'G03-01'], 'full', ''),
    ('audit-list', lambda n, p: n.startswith('qa-') or n in ('login', 'delete', 'create', 'update'), ['FL-07', 'P04-04'],
     'full', '审计行'),
    ('users-list', lambda n, p: n == '新建用户', ['E2E-15'], 'full', '对话框触发'),
    ('users-list', lambda n, p: n.startswith('搜索'), ['TABLE-USERS-SEARCH'], 'full', ''),
    ('users-list', lambda n, p: n in ('管理员', '操作员', '查看者'), ['E2E-15', 'FL-15', 'P04-06'], 'full', '行内角色下拉'),
    ('users-list', lambda n, p: n.startswith('qa-'), ['E2E-15'], 'full', '用户行'),
    ('dialog-new-user', lambda n, p: n in ('用户名', '密码', 'text', 'password'), ['E2E-15'], 'full', ''),
    ('dialog-new-user', lambda n, p: n in ('管理员', '操作员', '查看者'), ['E2E-15', 'FL-15'], 'full', '角色选择'),
    ('dialog-new-user', lambda n, p: n == '取消', ['E2E-15', 'G04-02'], 'full', '取消创建'),
    ('dialog-new-user', lambda n, p: n == '创建', ['E2E-15', 'G04-01'], 'full', '（本审计未提交）'),
    ('settings', lambda n, p: n in ('变更日志窗口', '心跳间隔（秒）', '最大日志字节数', '修订检查间隔（秒）'),
     ['FL-16'], 'full', '运行时设置'),
    ('settings', lambda n, p: n == '运行时设置', ['FL-16'], 'full', '运行时设置区块'),
    ('settings', lambda n, p: n == '保存', ['FL-16', 'G04-01'], 'full', '（本审计未提交）'),
    ('settings', lambda n, p: n in ('外观', '浅色', '深色', '系统'), ['G02-02'], 'full', ''),
    # 404 / error
    ('route-404', lambda n, p: True, ['ROUTE-35'], 'full', ''),
    ('route-error', lambda n, p: n == '重试', ['P05B-12', 'X07'], 'full', ''),
    ('route-error', lambda n, p: n in ('← 返回', '返回'), ['P05B-11', 'X07'], 'full', ''),
]

GENERIC = [
    (r'^跳转到第一页$', ['TABLE-{P}-PAGE', 'G03-07'], 'full', '分页'),
    (r'^跳转到最后一页$', ['TABLE-{P}-PAGE', 'G03-07', 'G03-08'], 'full', '分页'),
    (r'^上一页$', ['TABLE-{P}-PAGE', 'G03-07'], 'full', '分页'),
    (r'^下一页$', ['TABLE-{P}-PAGE', 'G03-07'], 'full', '分页'),
    (r'^跳转到第 \d+ 页 \d+$', ['TABLE-{P}-PAGE', 'G03-07'], 'full', '页码'),
    (r'^(10|20|30|40|50)$', ['TABLE-{P}-PAGE', 'G03-06'], 'full', '每页条数'),
    (r'^视图$', ['TABLE-{P}-COLUMNS', 'G03-05'], 'full', '列可见性'),
    (r': 升序$|: 降序$', ['TABLE-{P}-SORT', 'G03-04'], 'full', '列排序'),
]

PAGE_TABLE_KEY = {
    'nodes-list': 'NODES', 'groups-list': 'GROUPS', 'scripts-list': 'SCRIPTS', 'tasks-list': 'TASKS',
    'schedules-view': 'SCHEDULES', 'artifacts-list': 'ARTIFACTS', 'applications-list': 'APPLICATIONS',
    'executions-list': 'EXECUTIONS', 'transfers-list': 'TRANSFERS', 'audit-list': 'AUDIT', 'users-list': 'USERS',
}

def resolve_cases(ids, page_id):
    key = PAGE_TABLE_KEY.get(page_id, '')
    out = []
    for i in ids:
        if '{P}' in i:
            if not key:
                continue
            i = i.replace('{P}', key)
        if have(i) and i not in out:
            out.append(i)
    return out

def category_of(c, page_id):
    n, tag, role = c['name'], c['tag'], c.get('role') or ''
    if role in ('menuitemcheckbox',):
        return '切换'
    if role == 'option':
        return '输入'
    if role in ('menuitem', 'menuitemradio'):
        if n in ('删除', '取消传输', '撤销凭证', '移除'):
            return '破坏性'
        return '动作'
    if c.get('inDialog'):
        return '对话框内'
    if tag in ('input', 'textarea', 'select') or role in ('textbox', 'combobox', 'searchbox', 'spinbutton'):
        return '切换' if n == '显示密码' else '输入'
    if role == 'checkbox' or n in ('全选所有行', '选择该行'):
        return '切换'
    if n in ('语言', '切换主题', '个人资料', '切换侧边栏', 'Toggle Sidebar', '显示密码',
             '中文', 'English', '浅色', '深色', '系统'):
        return '切换'
    if n in ('删除', '取消传输', '撤销凭证', '移除'):
        return '破坏性'
    if n in ('保存', '创建', '登录', '开始传输', '上传', '部署', '生成安装命令', '立即运行'):
        return '表单提交'
    if tag == 'button' and (c.get('haspopup') == 'menu' or n in (
            '操作', '视图', '添加节点', '新建用户', '添加标签', '打开日志', '添加目标', '添加参数', '添加',
            '设置条件', '设置远程条件', '选择源 Agent', '选择目标 Agent', '新建脚本', '新建任务', '新建调度',
            '新建分组', '新建托管应用')):
        return '对话框触发'
    if tag == 'a':
        if n in NAV_NAMES or n in ('Skip to Main', 'NodeSteer Linux 节点自动化控制台'):
            return '导航'
        return '链接'
    if tag == 'button':
        return '动作'
    return '其他'

# ---------------------------------------------------------------- build
def load_controls(f):
    path = f'{BASE}/{f}.json'
    if not os.path.exists(path):
        return {}
    d = json.load(open(path, encoding='utf-8'))
    if not isinstance(d, dict):
        return {}
    out = {}
    for c in d.get('controls', []):
        n = (c.get('name') or '').strip()
        if not n:
            continue
        out[(n, c.get('role') or '', c.get('tag'))] = c
    return out

base_cache = {}
for pg in PAGES:
    base_cache[pg['id']] = None
for pg in PAGES:
    if pg.get('base'):
        base_cache[pg['id']] = load_controls(pg['base'])
        base_cache[pg['id']] = {(k[0], k[1]) for k in base_cache[pg['id']].keys()}

pages_out = []
total_controls = covered_n = uncovered_n = 0
for pg in PAGES:
    seen = {}
    for f in pg['files']:
        for k, c in load_controls(f).items():
            seen.setdefault(k, c)
    controls = []
    for (n, role, tag), c in seen.items():
        if pg['id'] != 'global-shell' and n in SHELL_NAMES and not c.get('inDialog'):
            continue  # shell 控件统一归入 global-shell 页
        if pg.get('base'):
            bset = base_cache.get(pg['id'])
            if bset and (n, role) in bset and not c.get('inDialog'):
                continue  # 菜单/对话框页只保留增量控件
        cat = category_of(c, pg['id'])
        hit = None
        for pid, m, ids, lvl, note in R:
            if pid != '*' and pid != pg['id']:
                continue
            try:
                if m(n, pg['id']):
                    hit = (ids, lvl, note)
                    break
            except Exception:
                continue
        if hit is None:
            for pat, ids, lvl, note in GENERIC:
                if re.search(pat, n):
                    hit = (ids, lvl, note)
                    break
        if hit is None:
            hit = ([], 'none', '')
        ids, lvl, note = hit
        ids = resolve_cases(ids, pg['id'])
        status = 'covered' if (ids and lvl != 'none') else 'uncovered'
        st = status_of(ids)
        if status == 'uncovered':
            verdict = '未覆盖'
        elif lvl in ('partial', 'fixture'):
            verdict = '部分覆盖'
        elif 'PASS' in st.values():
            verdict = '已覆盖'
        else:
            verdict = '部分覆盖'
        gap_tier = None
        if verdict == '未覆盖':
            gap_tier = '需授权' if cat in ('破坏性', '表单提交') else '必须补测'
        elif lvl == 'fixture':
            gap_tier = '需夹具'
        elif lvl == 'partial' or verdict == '部分覆盖':
            gap_tier = '必须补测'
        controls.append({
            'name': n, 'tag': tag, 'role': role, 'type': c.get('type') or '',
            'href': c.get('href') or '', 'category': cat,
            'coverage': status, 'coverage_level': lvl if ids else None,
            'coverage_verdict': verdict,
            'gap_tier': gap_tier,
            'cases': ids, 'case_status': st,
            'in_table': bool(c.get('inTable')), 'in_dialog': bool(c.get('inDialog')),
            'note': note,
        })
    controls.sort(key=lambda x: (x['category'], x['name']))
    cov = sum(1 for c in controls if c['coverage'] == 'covered')
    total_controls += len(controls); covered_n += cov; uncovered_n += len(controls) - cov
    pages_out.append({
        'page_id': pg['id'], 'route': pg['route'], 'title': pg['title'], 'state': pg['state'],
        'files': pg['files'], 'evidence': pg['evidence'], 'note': pg['note'],
        'controls_total': len(controls), 'covered': cov, 'uncovered': len(controls) - cov,
        'controls': controls,
    })

# ---------------------------------------------------------------- cross-page linkage (live-verified 2026-10-17)
CROSS_LINKAGE = [
    dict(id='L01', flow='仪表盘 · 最近执行 查看全部 → /executions', result='实测通过', evidence='nav-dash-viewall-exec',
         cases=['P03-02', 'P03-06'], gap=None),
    dict(id='L02', flow='仪表盘 · 调度 查看全部 → 调度视图（最终 URL /schedules）', result='实测通过', evidence='nav-dash-viewall-sched',
         cases=['P03-07', 'ROUTE-19'], gap=None),
    dict(id='L03', flow='仪表盘 · 节点 查看全部 → /agents', result='实测通过', evidence='nav-dash-viewall-agents',
         cases=['P03-03'], gap=None),
    dict(id='L04', flow='仪表盘 · 应用 查看全部 → /applications', result='实测通过', evidence='nav-dash-viewall-apps',
         cases=['P03-04'], gap=None),
    dict(id='L05', flow='仪表盘 · 应用健康 行链接 → /applications/{id}', result='实测通过', evidence='nav-dash-app-link',
         cases=['P03-04'], gap=None),
    dict(id='L06', flow='仪表盘 · 最近执行 行名 → /executions/{id}', result='实测通过', evidence='nav-dash-exec-link',
         cases=['P03-06'], gap=None),
    dict(id='L07', flow='节点列表 行名 → /agents/{id}', result='实测通过', evidence='nav-nodes-row-link',
         cases=['P04-03'], gap=None),
    dict(id='L08', flow='节点详情 执行表 查看详情 → /executions/{id}', result='实测通过', evidence='nav-node-detail-viewdetail',
         cases=['P05B-10'], gap=None),
    dict(id='L09', flow='脚本列表 行名 → /scripts/{id}（编辑脚本）', result='实测通过', evidence='nav-scripts-row-link',
         cases=['P07_P08-08'], gap=None),
    dict(id='L10', flow='任务列表 行名 → /tasks/{id}', result='捕获到链接（10 行时）；并发清理后任务为空',
         evidence='evidence/tasks-list-rowlinks-20261017.tsv', cases=['P09P11-02'], gap=None),
    dict(id='L11', flow='任务列表 立即运行 → /tasks/{id}/run', result='捕获到链接；本审计未点击（写操作）',
         evidence='evidence/tasks-list-rowlinks-20261017.tsv', cases=['P11-01', 'P11-02'], gap=None),
    dict(id='L12', flow='任务详情 编辑 → /tasks/{id}/edit', result='未验证（任务被并发清理）', evidence=None,
         cases=['ROUTE-16'], gap='需夹具（未验证）'),
    dict(id='L13', flow='任务详情 新建调度 → /schedules/new?task_id=', result='未验证（任务被并发清理）', evidence=None,
         cases=['X05'], gap='需夹具（未验证）'),
    dict(id='L14', flow='应用列表 行名 → /applications/{id}', result='实测通过', evidence='nav-apps-row-link',
         cases=['P14P15-07'], gap=None),
    dict(id='L15', flow='发布包列表 Usage 列 → /applications/{id}', result='实测通过', evidence='nav-artifacts-usage',
         cases=['FL-08'], gap=None),
    dict(id='L16', flow='应用编辑页 发布包管理 → /artifacts', result='实测通过', evidence='nav-appdetail-artifacts',
         cases=['FL-08', 'X06'], gap=None),
    dict(id='L17', flow='执行列表 任务/节点/查看详情 三类链接', result='实测通过', evidence='nav-execs-viewdetail',
         cases=['P17P18-06'], gap=None),
    dict(id='L18', flow='执行详情 任务/节点回链（真实任务执行）', result='实测通过（“未知任务”执行无回链，符合数据状态）',
         evidence='execution-detail-real', cases=['P17P18-06', 'P05B-10'], gap=None),
    dict(id='L19', flow='节点详情 任务 / 调度 / 托管应用 分区回链', result='当前 UI 未渲染这些分区（仅执行表）',
         evidence='node-detail', cases=['P05B-07', 'P05B-08', 'P05B-09'], gap='确认缺失（节点详情未渲染关联分区）'),
    dict(id='L20', flow='审计列表 资源 ID → 资源详情', result='无链接（纯文本），无法从审计跳转资源', evidence='audit-list',
         cases=[], gap='确认缺失（无链接/无路由，无对应用例）'),
    dict(id='L21', flow='文件传输 → 传输详情/日志', result='无详情页路由（产品缺口）', evidence='discovery.json',
         cases=[], gap='确认缺失（无链接/无路由，无对应用例）'),
    dict(id='L22', flow='用户列表 → 用户详情', result='无详情页路由（产品缺口）', evidence='discovery.json',
         cases=[], gap='确认缺失（无链接/无路由，无对应用例）'),
    dict(id='L23', flow='发布包 → 发布包详情', result='无详情页路由（产品缺口，仅行菜单）', evidence='artifacts-list',
         cases=[], gap='确认缺失（无链接/无路由，无对应用例）'),
]

DATATABLE_RISK = {
    'defect': 'WEB-NODE-SELECTION-DRIFT-20261017（P1，X14 FAIL）',
    'mechanism': '共享 DataTable 的行选择状态 rowSelection 由 TanStack 默认行 ID（行下标）承载，未设置 getRowId；'
                 '批量操作读取 getFilteredSelectedRowModel().rows.map(row=>row.original)，'
                 '因此过滤/排序/翻页/增删行后，勾选会按位置漂移到其它行。',
    'code_evidence': [
        'web/src/features/shared/data-table.tsx:146（rowSelection state，无 getRowId）',
        'web/src/features/shared/data-table.tsx:157（enableRowSelection 时注入 selectColumn）',
        'web/src/features/catalog/index.tsx:301（enableRowSelection={canWrite || canRun}）',
        'web/src/features/catalog/index.tsx:303-309（renderBulkActions 用 getFilteredSelectedRowModel）',
    ],
    'pages_with_row_selection': ['/agents（节点列表，唯一启用行选择的表）'],
    'pages_without_row_selection': [
        '/tasks', '/tasks?view=schedules', '/scripts', '/groups', '/applications', '/artifacts',
        '/executions', '/transfers', '/audit', '/users',
    ],
    'verdict': '当前仅 /agents 具备行选择 + 批量工具条，其余 10 个复用表未渲染选择列，故漂移缺陷在当前构建下只可能出现在节点表；'
               '但漂移是共享组件层的结构性问题（无 getRowId），一旦任何其它表开启 enableRowSelection 会同样复现。',
    'readonly_checks': [
        '逐页 DOM 校验：仅 /agents 出现“全选所有行/选择该行”控件（7 个），其余表 0 个。',
        '代码校验：enableRowSelection 仅在 catalog/index.tsx:301 传入。',
        'RBAC：viewer 账号（canWrite/canRun=false）不渲染选择列（未在本轮单独会话复测）。',
    ],
    'not_verified': '未执行删除/过滤后勾选的破坏性复现（只读约束），漂移复现以 X14 既有失败证据为准。',
}

FIXTURE_REQUIRED = [
    dict(route='/groups/{id}', title='编辑分组', cases=['ROUTE-09', 'P06-04'], reason='环境 0 分组'),
    dict(route='/schedules/{id}', title='编辑调度', cases=['ROUTE-22', 'P12P13-10'], reason='环境 0 调度'),
    dict(route='/tasks/{id}/edit', title='编辑任务', cases=['ROUTE-16', 'X09'], reason='任务被并发 QA 清理（捕获时为错误态）'),
    dict(route='/tasks/{id}/run', title='立即运行', cases=['ROUTE-17', 'P11-01'], reason='任务被并发 QA 清理（捕获时为错误态）'),
    dict(route='/agents/{id} · 任务/调度/应用分区', title='节点详情关联分区', cases=['P05B-07', 'P05B-08', 'P05B-09'],
         reason='当前 UI 未渲染这些分区（需先确认产品是否仍要求）'),
    dict(route='/transfers · 失败/进行中行', title='传输重试/取消菜单', cases=['E2E-14', 'X18'], reason='需失败或进行中的传输夹具（已捕获菜单形态）'),
    dict(route='/groups · 有数据', title='分组列表工具栏/行菜单', cases=['TABLE-GROUPS-*', 'P06-01'], reason='环境 0 分组（仅空态）'),
    dict(route='/tasks?view=schedules · 有数据', title='调度视图工具栏/行菜单', cases=['TABLE-SCHEDULES-*', 'P12P13-02'], reason='环境 0 调度（仅空态）'),
]

KNOWN_ABSENT = [
    dict(item='节点标签删除/编辑入口', cases=['P05B-06'], note='页面仅有“添加标签”，无删除/编辑（与历史 OBS-001 一致）'),
    dict(item='用户删除入口', cases=[], note='用户列表无删除按钮（后端亦未提供 DELETE）'),
    dict(item='用户密码重置入口', cases=[], note='无 UI 入口'),
    dict(item='文件传输详情页', cases=[], note='无路由'),
    dict(item='发布包详情页', cases=[], note='无路由（仅行菜单 + Usage 列）'),
    dict(item='应用手动回滚入口', cases=['AR-29'], note='无 UI 入口（回滚为 Agent 侧自动）'),
    dict(item='执行日志独立对话框', cases=['P17P18-08'], note='“打开日志”实为 scrollIntoView 到页内日志区'),
]

result = {
    'schema_version': 1,
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'auditor': 'subagent control-level coverage audit (read-only, non-destructive)',
    'build_label': 'dddee4f0-e2e-repair-20261016-cr1',
    'ui': 'http://192.168.100.209:18080',
    'cases_source': '.e2e/runs/20260930-101945/repair/feature-inventory.json',
    'cases_total': len(CASES),
    'pages_total': len(pages_out),
    'controls_total': total_controls,
    'covered_total': covered_n,
    'uncovered_total': uncovered_n,
    'cross_page_linkage': CROSS_LINKAGE,
    'datatable_reuse_risk': DATATABLE_RISK,
    'fixture_required': FIXTURE_REQUIRED,
    'known_absent_controls': KNOWN_ABSENT,
    'pages': pages_out,
}
json.dump(result, open(f'{OUT_DIR}/control-inventory.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps({k: result[k] for k in ('pages_total', 'controls_total', 'covered_total', 'uncovered_total')},
                 ensure_ascii=False))
for p in pages_out:
    print(f"{p['page_id']:24s} {p['state']:15s} {p['controls_total']:3d}  covered={p['covered']:3d} uncovered={p['uncovered']:3d}")
