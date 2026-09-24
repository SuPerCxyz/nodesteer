import { describe, expect, it } from 'vitest'
import {
  CRON_FIELD_SPECS,
  CRON_PRESETS,
  isValidCronExpression,
  joinFields,
  matchPreset,
  normalizeCronNames,
  parseCronToFields,
  validateCronField,
} from './cron-fields'

const [minute, _hour, dayOfMonth, month, dayOfWeek] = CRON_FIELD_SPECS

describe('validateCronField 字段语法', () => {
  it('支持单值 / 列表 / 区间 / 步进', () => {
    expect(validateCronField('*', minute)).toBe(true)
    expect(validateCronField('30', minute)).toBe(true)
    expect(validateCronField('1,15', minute)).toBe(true)
    expect(validateCronField('1-5', minute)).toBe(true)
    expect(validateCronField('*/15', minute)).toBe(true)
    expect(validateCronField('1-30/5', minute)).toBe(true)
    expect(validateCronField('5/15', minute)).toBe(true)
    expect(validateCronField('?', dayOfMonth)).toBe(true)
  })

  it('拒绝越界、空字段、倒置区间与非法步进', () => {
    expect(validateCronField('99', minute)).toBe(false)
    expect(validateCronField('', minute)).toBe(false)
    expect(validateCronField('0', dayOfMonth)).toBe(false)
    expect(validateCronField('13', month)).toBe(false)
    // robfig v3 的 dow 上界是 6，7 同样非法（与后端口径一致）
    expect(validateCronField('7', dayOfWeek)).toBe(false)
    expect(validateCronField('5-1', minute)).toBe(false)
    expect(validateCronField('*/0', minute)).toBe(false)
    expect(validateCronField('1,,15', minute)).toBe(false)
    expect(validateCronField('1, 15', minute)).toBe(false)
    expect(validateCronField('1-2-3', minute)).toBe(false)
  })

  it('接受月/周名称并归一到数值范围', () => {
    expect(validateCronField('jan-mar', month)).toBe(true)
    expect(validateCronField('MON-FRI', dayOfWeek)).toBe(true)
    expect(validateCronField('sun-sat', dayOfWeek)).toBe(true)
    expect(validateCronField('XXX', month)).toBe(false)
  })
})

describe('parseCronToFields / joinFields 字段↔表达式同步', () => {
  it('合法 5 段表达式拆成 5 个字段，join 后等价', () => {
    const fields = parseCronToFields('0 9 * * 1-5')
    expect(fields).toEqual(['0', '9', '*', '*', '1-5'])
    expect(joinFields(fields!)).toBe('0 9 * * 1-5')
  })

  it('容忍多余空白', () => {
    expect(parseCronToFields('  0   *   *   *   *  ')).toEqual([
      '0',
      '*',
      '*',
      '*',
      '*',
    ])
  })

  it('非法表达式返回 null 而不是抛错', () => {
    expect(parseCronToFields('foo')).toBeNull()
    expect(parseCronToFields('99 * * * *')).toBeNull()
    expect(parseCronToFields('* * * *')).toBeNull()
    expect(parseCronToFields('0 0 0 1 1 *')).toBeNull()
    expect(parseCronToFields('')).toBeNull()
  })

  it('TZ/CRON_TZ 前缀返回 null，避免重新生成时丢掉前缀', () => {
    expect(parseCronToFields('CRON_TZ=Asia/Shanghai 0 9 * * *')).toBeNull()
    expect(parseCronToFields('TZ=UTC 0 9 * * *')).toBeNull()
  })
})

describe('isValidCronExpression 提交口径', () => {
  it('放行合法 5 段表达式与 robfig 描述符', () => {
    expect(isValidCronExpression('0 9 * * 1-5')).toBe(true)
    expect(isValidCronExpression('*/15 * * * *')).toBe(true)
    expect(isValidCronExpression('0 0 30 2 *')).toBe(true)
    expect(isValidCronExpression('0 0 * * ?')).toBe(true)
    expect(isValidCronExpression('@daily')).toBe(true)
    expect(isValidCronExpression('@hourly')).toBe(true)
    expect(isValidCronExpression('@every 1h30m')).toBe(true)
    expect(isValidCronExpression('CRON_TZ=Asia/Shanghai 0 9 * * *')).toBe(true)
  })

  it('拦截会让后端 400 的输入', () => {
    expect(isValidCronExpression('')).toBe(false)
    expect(isValidCronExpression('   ')).toBe(false)
    expect(isValidCronExpression('* * * *')).toBe(false)
    expect(isValidCronExpression('0 0 0 1 1 *')).toBe(false)
    expect(isValidCronExpression('*/0 * * * *')).toBe(false)
    expect(isValidCronExpression('99 * * * *')).toBe(false)
    expect(isValidCronExpression('0 0 * * 7')).toBe(false)
    expect(isValidCronExpression('@reboot')).toBe(false)
    expect(isValidCronExpression('@every')).toBe(false)
    expect(isValidCronExpression('foo bar')).toBe(false)
  })
})

describe('matchPreset 预设匹配', () => {
  it('命中预设返回 id，自定义表达式返回 null', () => {
    expect(matchPreset('0 * * * *')).toBe('hourly')
    expect(matchPreset('  0   9 * * 1-5 ')).toBe('weekday9')
    expect(matchPreset('7 8 9 10 11')).toBeNull()
    expect(CRON_PRESETS).toHaveLength(8)
  })
})

describe('normalizeCronNames 名称归一', () => {
  it('把月/周名称替换为数字，供 cron-parser 计算', () => {
    expect(normalizeCronNames('0 9 * * MON-FRI')).toBe('0 9 * * 1-5')
    expect(normalizeCronNames('0 9 * jan-mar *')).toBe('0 9 * 1-3 *')
    expect(normalizeCronNames('@daily')).toBeNull()
    expect(normalizeCronNames('TZ=UTC 0 9 * * *')).toBeNull()
  })
})
