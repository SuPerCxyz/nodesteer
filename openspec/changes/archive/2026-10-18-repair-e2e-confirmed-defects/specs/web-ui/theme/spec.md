## ADDED Requirements

### Requirement: 系统主题与浏览器颜色同步
Web SHALL 在 light/dark/system、系统媒体变化、重载和重置时同步页面解析主题与 theme-color；系统变化不得覆盖用户明确选择。

#### Scenario: system 模式暗色变化
- **WHEN** system 模式的系统偏好变为暗色或暗色模式重载
- **THEN** 页面class、实际背景和theme-color均为暗色；明确light选择保持light
