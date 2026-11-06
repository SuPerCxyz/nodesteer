import '@/styles/index.css'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page as browserPage, userEvent } from 'vitest/browser'
import { RackField, RackList, RackRow, RowText } from './rack-list'

afterEach(async () => {
  await browserPage.viewport(1280, 720)
})

describe('RackList / RackRow', () => {
  it('renders a semantic list without table markup', async () => {
    const page = await render(
      <RackList testId='rack'>
        <RackRow
          title='node-a'
          subtitle='在线'
          fields={[
            {
              label: 'CPU',
              value: '12.5%',
              bar: { value: 12.5, testId: 'cpu-bar' },
            },
          ]}
          tags={[{ label: '负载', value: '1.0/2.0/3.0' }]}
          actions={<button type='button'>详情</button>}
        />
      </RackList>
    )

    await expect.element(page.getByRole('list')).toBeVisible()
    await expect.element(page.getByRole('listitem')).toBeVisible()
    await expect.element(page.getByText('node-a')).toBeVisible()
    await expect.element(page.getByText('在线')).toBeVisible()
    await expect.element(page.getByText('CPU')).toBeVisible()
    await expect.element(page.getByText('12.5%')).toBeVisible()
    await expect.element(page.getByText('负载')).toBeVisible()
    await expect
      .element(page.getByRole('button', { name: '详情' }))
      .toBeVisible()
    expect(page.container.querySelector('table')).toBeNull()
    expect(
      getComputedStyle(page.getByText('12.5%', { exact: true }).element())
        .fontSize
    ).toBe('12px')
    expect(page.getByTestId('cpu-bar').element().style.width).toBe('12.5%')
  })

  it.each([
    [3, 60],
    [4, 60],
    [5, 64],
    [6, 64],
    [8, 64],
  ])('%i 个字段使用目标行高 %ipx', async (count, expected) => {
    await browserPage.viewport(1280, 720)
    const page = await render(
      <RackList>
        <RackRow
          testId='height-row'
          title='node-a'
          subtitle='在线'
          fields={Array.from({ length: count }, (_, index) => ({
            label: `F${index}`,
            value: `value-${index}`,
          }))}
        />
      </RackList>
    )
    const height = page
      .getByTestId('height-row')
      .element()
      .getBoundingClientRect().height
    expect(Math.abs(height - expected)).toBeLessThanOrEqual(1.5)
    const longValue = 'x'.repeat(240)
    const truncated = await render(
      <RackList>
        <RackRow
          title='node-b'
          fields={[{ label: 'Long', value: longValue }]}
        />
      </RackList>
    )
    const value = truncated.getByText(longValue).element()
    expect(value.getAttribute('title')).toBe(longValue)
    expect(getComputedStyle(value).overflow).toBe('hidden')
    expect(getComputedStyle(value).whiteSpace).toBe('nowrap')
    expect(getComputedStyle(value).textOverflow).toBe('ellipsis')
    await truncated.unmount()
  })

  it('supports row selection', async () => {
    const onSelectedChange = vi.fn()
    const page = await render(
      <RackRow
        title='node-b'
        selected={false}
        onSelectedChange={onSelectedChange}
      />
    )

    await userEvent.click(page.getByRole('checkbox', { name: '选择该行' }))
    expect(onSelectedChange).toHaveBeenCalledWith(true)
  })

  it('keeps node field and bar columns aligned when tag values differ', async () => {
    const page = await render(
      <RackList>
        <RackRow
          layout='node'
          testId='node-row-a'
          title='native-node-1'
          fields={[
            {
              label: 'CPU',
              value: '3.7%',
              bar: { value: 3.7, testId: 'node-a-cpu-bar' },
            },
            {
              label: 'MEM',
              value: '68.4%',
              bar: { value: 68.4, testId: 'node-a-mem-bar' },
            },
          ]}
          tags={[
            { label: '负载', value: '1.16 / 0.83 / 0.94' },
            { label: '网络', value: '↓ 52.5 KB/s ↑ 313.2 KB/s' },
          ]}
          actions={<button type='button'>查看详情</button>}
        />
        <RackRow
          layout='node'
          testId='node-row-b'
          title='test-preview'
          fields={[
            {
              label: 'CPU',
              value: '0.8%',
              bar: { value: 0.8, testId: 'node-b-cpu-bar' },
            },
            {
              label: 'MEM',
              value: '70.9%',
              bar: { value: 70.9, testId: 'node-b-mem-bar' },
            },
          ]}
          tags={[
            { label: '负载', value: '0.00 / 0.00 / 0.00' },
            { label: '网络', value: '↓ 362 B/s ↑ 851 B/s' },
          ]}
          actions={<button type='button'>查看详情</button>}
        />
      </RackList>
    )

    const template = (testId: string) =>
      getComputedStyle(page.getByTestId(testId).element()).gridTemplateColumns
    expect(template('node-row-a')).toBe(template('node-row-b'))

    const left = (testId: string) =>
      Math.round(
        page.getByTestId(testId).element().getBoundingClientRect().left
      )
    expect(left('node-a-cpu-bar')).toBe(left('node-b-cpu-bar'))
    expect(left('node-a-mem-bar')).toBe(left('node-b-mem-bar'))
  })

  it('renders an 8px track with a visible ring and tone fill', async () => {
    const page = await render(
      <RackField
        label='CPU'
        value='78.0%'
        tone='warning'
        bar={{ value: 78, tone: 'warning', testId: 'tone-bar' }}
      />
    )
    const bar = page.getByTestId('tone-bar').element()
    const track = bar.parentElement
    if (!track) throw new Error('bar track missing')
    expect(track.className).toContain('h-2')
    expect(track.className).toContain('ring-1')
    expect(track.className).toContain('bg-muted-foreground/20')
    expect(bar.className).toContain('bg-warning')
  })

  it('clamps progress bars and applies tone colors', async () => {
    const page = await render(
      <RackList>
        <RackRow
          title='node-c'
          fields={[
            {
              label: 'CPU',
              value: '120%',
              tone: 'danger',
              bar: { value: 120, tone: 'danger', testId: 'danger-bar' },
            },
          ]}
        />
      </RackList>
    )

    const bar = page.getByTestId('danger-bar').element()
    expect(bar.style.width).toBe('100%')
    expect(bar.className).toContain('bg-danger')
  })
  it('centers single/two-line blocks with consistent typography and keeps long content single-line', async () => {
    await browserPage.viewport(1366, 768)
    const action = vi.fn()
    const longText = 'long-content-'.repeat(45)
    const page = await render(
      <RackList>
        <RackRow
          testId='one'
          title='Primary single'
          fields={[{ label: 'Owner', value: 'Alex' }]}
          actions={<button onClick={action}>Open single</button>}
        />
        <RackRow
          testId='two'
          title={
            <RowText primary='Primary double' secondary='Secondary detail' />
          }
          fields={[
            {
              label: 'Owner',
              value: <RowText primary='Alex' secondary='Team detail' />,
            },
          ]}
          actions={<button>Open double</button>}
        />
        <RackRow
          testId='long'
          title={
            <a className='block truncate' href='#long' title={longText}>
              {longText}
            </a>
          }
          subtitle='Secondary remains readable'
          fields={[
            { label: 'Empty', value: '' },
            {
              label: 'Long descriptive field label',
              value: 'long-field-value-'.repeat(8),
            },
          ]}
          actions={<button>Open long</button>}
        />
      </RackList>
    )
    const one = page.getByTestId('one').element()
    const two = page.getByTestId('two').element()
    expect(
      Math.abs(
        one.getBoundingClientRect().height - two.getBoundingClientRect().height
      )
    ).toBeLessThan(1.5)
    for (const row of [one, two]) {
      const blocks = ['row-identity', 'row-field', 'row-actions'].map((slot) =>
        row.querySelector(`[data-slot="${slot}"]`)!.getBoundingClientRect()
      )
      const centers = blocks.map((rect) => rect.top + rect.height / 2)
      expect(Math.max(...centers) - Math.min(...centers)).toBeLessThan(1.5)
      expect(blocks.every((rect) => rect.height >= 24)).toBe(true)
    }
    const primary = getComputedStyle(
      page.getByText('Primary double', { exact: true }).element()
    )
    const secondary = getComputedStyle(
      page.getByText('Secondary detail', { exact: true }).element()
    )
    const label = getComputedStyle(
      page.getByText('Owner', { exact: true }).first().element()
    )
    const ordinaryValue = getComputedStyle(
      page.getByText('Alex', { exact: true }).first().element()
    )
    expect([label.fontSize, label.lineHeight]).toEqual(['12px', '16px'])
    expect([ordinaryValue.fontSize, ordinaryValue.lineHeight]).toEqual([
      '14px',
      '20px',
    ])
    expect([primary.fontSize, primary.lineHeight]).toEqual(['14px', '20px'])
    expect([secondary.fontSize, secondary.lineHeight]).toEqual(['12px', '16px'])
    await userEvent.click(page.getByRole('button', { name: 'Open single' }))
    expect(action).toHaveBeenCalledTimes(1)
    await browserPage.viewport(390, 844)
    const longRow = page.getByTestId('long').element()
    expect(longRow.scrollWidth).toBeLessThanOrEqual(longRow.clientWidth + 1)
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth + 1
    )
    await expect
      .element(page.getByText('Secondary remains readable'))
      .toBeVisible()
    await expect.element(page.getByText('—', { exact: true })).toBeVisible()
    await expect
      .element(page.getByText('Long descriptive field label', { exact: true }))
      .toBeVisible()
    const button = page
      .getByRole('button', { name: 'Open long' })
      .element() as HTMLButtonElement
    button.focus()
    expect(document.activeElement).toBe(button)
  })
})
