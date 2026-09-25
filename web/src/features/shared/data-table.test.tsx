import type { ColumnDef } from '@tanstack/react-table'
import i18n from '@/i18n'
import '@/styles/index.css'
import { beforeAll, describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { DataTable } from './data-table'

type Row = { id: string; name: string }

const columns: ColumnDef<Row>[] = [
  { accessorKey: 'name', header: '名称', size: 160, minSize: 120 },
  {
    id: 'actions',
    header: '',
    enableHiding: false,
    size: 72,
    minSize: 64,
    cell: () => <span>-</span>,
  },
]

const data: Row[] = [{ id: 'a', name: 'node-a' }]

async function renderTable(enableRowSelection: boolean) {
  return await render(
    <DataTable
      data={data}
      columns={columns}
      hideSearch
      enableRowSelection={enableRowSelection}
      bulkEntityName='节点'
      renderBulkActions={(table) => (
        <span>已选 {table.getSelectedRowModel().rows.length} 行</span>
      )}
    />
  )
}

describe('DataTable 行多选基建', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('未勾选时不显示批量工具条，业务列保持可见', async () => {
    const { getByRole, getByText } = await renderTable(true)

    await expect.element(getByText('node-a')).toBeVisible()
    await expect.element(getByRole('toolbar')).not.toBeInTheDocument()
  })

  it('勾选行后浮出批量工具条并显示选中数，取消勾选后消失', async () => {
    const { getByRole, getByText } = await renderTable(true)
    const rowCheckbox = getByRole('checkbox', { name: '选择该行' })

    await expect.element(rowCheckbox).toBeVisible()
    await expect.element(getByText('node-a')).toBeVisible()
    await userEvent.click(rowCheckbox)

    const toolbar = getByRole('toolbar')
    await expect.element(toolbar).toBeVisible()
    await expect
      .element(getByRole('checkbox', { name: '全选所有行' }))
      .toBeVisible()

    await userEvent.click(rowCheckbox)
    await expect.element(toolbar).not.toBeInTheDocument()
  })

  it('未启用多选时不注入选择列，也没有工具条', async () => {
    const { getByRole, getByText } = await renderTable(false)

    await expect.element(getByText('node-a')).toBeVisible()
    await expect.element(getByRole('toolbar')).not.toBeInTheDocument()
    await expect
      .element(getByRole('checkbox', { name: '全选所有行' }))
      .not.toBeInTheDocument()
    await expect
      .element(getByRole('checkbox', { name: '选择该行' }))
      .not.toBeInTheDocument()
  })
})

describe('DataTable 列宽与表头对齐', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('表格最小宽度等于各列最小宽之和，容器可容纳时不横向滚动', async () => {
    const { container } = await renderTable(false)
    const table = container.querySelector('table') as HTMLTableElement
    // 名称 minSize 120 + 操作 minSize 64
    expect(table.style.minWidth).toBe('184px')

    const scroller = table.parentElement as HTMLDivElement
    expect(table.getBoundingClientRect().width).toBeLessThanOrEqual(
      scroller.clientWidth + 1
    )
  })

  it('可排序表头标签与单元格内容同左基线', async () => {
    const { container } = await renderTable(false)
    const th = container.querySelector('thead th') as HTMLTableCellElement
    const label = th.querySelector('button span') as HTMLSpanElement
    const td = container.querySelector('tbody td') as HTMLTableCellElement

    const range = document.createRange()
    range.selectNodeContents(td.firstChild as Node)
    const cellLeft = range.getBoundingClientRect().left

    expect(
      Math.abs(label.getBoundingClientRect().left - cellLeft)
    ).toBeLessThanOrEqual(1)
  })
})
