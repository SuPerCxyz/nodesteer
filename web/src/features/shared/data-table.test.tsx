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

describe('DataTable 加载态与空态区分', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('isLoading 且无数据时显示加载占位，不显示「没有结果」', async () => {
    const { getByText } = await render(
      <DataTable data={[]} columns={columns} hideSearch isLoading />
    )

    await expect.element(getByText('加载中...')).toBeVisible()
    await expect.element(getByText('没有结果。')).not.toBeInTheDocument()
  })

  it('加载完成后的空数据仍显示「没有结果」，不被加载态覆盖', async () => {
    const { getByText } = await render(
      <DataTable data={[]} columns={columns} hideSearch />
    )

    await expect.element(getByText('没有结果。')).toBeVisible()
    await expect.element(getByText('加载中...')).not.toBeInTheDocument()
  })
})

describe('DataTable 服务端搜索', () => {
  it('受控搜索传给调用方，服务端结果不被当前页本地重复过滤', async () => {
    const changes: string[] = []
    const { getByRole, getByText } = await render(
      <DataTable
        data={data}
        columns={columns}
        manualPagination
        manualFiltering
        total={1}
        filter='server-only-match'
        onFilterChange={(value) => changes.push(value)}
        searchPlaceholder='服务端搜索'
      />
    )
    await expect.element(getByText('node-a')).toBeVisible()
    const input = getByRole('textbox', { name: '服务端搜索' })
    await expect.element(input).toHaveValue('server-only-match')
    await userEvent.fill(input, 'new-search')
    expect(changes[changes.length - 1]).toBe('new-search')
  })
})

describe('DataTable 行选择按行 id 而非行位置', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  const pageA: Row[] = [
    { id: 'a', name: 'node-a' },
    { id: 'b', name: 'node-b' },
  ]
  const pageB: Row[] = [
    { id: 'c', name: 'node-c' },
    { id: 'd', name: 'node-d' },
  ]

  /** 记录批量操作收到的行对象 id，验证命中的是行而不是行位置 */
  function tableWithProbe(rows: Row[], seen: string[][]) {
    return (
      <DataTable
        data={rows}
        columns={columns}
        hideSearch
        enableRowSelection
        bulkEntityName='节点'
        renderBulkActions={(table) => {
          seen.push(
            table
              .getFilteredSelectedRowModel()
              .rows.map((row) => (row.original as Row).id)
          )
          return <span>已选 {table.getSelectedRowModel().rows.length} 行</span>
        }}
      />
    )
  }

  it('同页数据重排后勾选跟随行 id，不跟随行位置', async () => {
    const seen: string[][] = []
    const page = await render(tableWithProbe(pageA, seen))

    // 勾选第 1 行（node-a）
    await userEvent.click(page.getByRole('checkbox', { name: '选择该行' }).nth(0))
    expect(seen[seen.length - 1]).toEqual(['a'])

    // 数据重排：node-a 从位置 0 移到位置 1
    await page.rerender(tableWithProbe([pageA[1], pageA[0]], seen))

    // 位置 0 现在是 node-b，必须保持未勾选；勾选仍在 node-a（位置 1）
    await expect
      .element(page.getByRole('checkbox', { name: '选择该行' }).nth(0))
      .not.toBeChecked()
    await expect
      .element(page.getByRole('checkbox', { name: '选择该行' }).nth(1))
      .toBeChecked()
    expect(seen[seen.length - 1]).toEqual(['a'])
  })

  it('翻到另一页后不把选择漂移到同位置的行，返回原页仍保留选择', async () => {
    const seen: string[][] = []
    const page = await render(tableWithProbe(pageA, seen))

    await userEvent.click(page.getByRole('checkbox', { name: '选择该行' }).nth(0))
    expect(seen[seen.length - 1]).toEqual(['a'])

    // 翻页：新一页的同位置行不得被当成已选，批量工具条随之为空
    await page.rerender(tableWithProbe(pageB, seen))
    await expect
      .element(page.getByRole('checkbox', { name: '选择该行' }).nth(0))
      .not.toBeChecked()
    await expect.element(page.getByRole('toolbar')).not.toBeInTheDocument()
    expect(seen[seen.length - 1]).toEqual([])

    // 返回原页：选择按 id 保留
    await page.rerender(tableWithProbe(pageA, seen))
    await expect
      .element(page.getByRole('checkbox', { name: '选择该行' }).nth(0))
      .toBeChecked()
    expect(seen[seen.length - 1]).toEqual(['a'])
  })

  it('自定义 getRowId 覆盖默认取 id 的行为', async () => {
    const seen: string[][] = []
    const rows: Row[] = [
      { id: 'a', name: 'node-a' },
      { id: 'b', name: 'node-b' },
    ]
    const page = await render(
      <DataTable
        data={rows}
        columns={columns}
        hideSearch
        enableRowSelection
        getRowId={(row) => `custom:${row.name}`}
        renderBulkActions={(table) => {
          seen.push(
            table
              .getFilteredSelectedRowModel()
              .rows.map((row) => (row.original as Row).id)
          )
          return <span>已选 {table.getSelectedRowModel().rows.length} 行</span>
        }}
      />
    )

    await userEvent.click(page.getByRole('checkbox', { name: '选择该行' }).nth(1))
    expect(seen[seen.length - 1]).toEqual(['b'])
  })
})

describe('DataTable 每页条数越界显示（OBS-20261014-PAGESIZE-PERSISTED-OUT-OF-RANGE-BLANK）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  function tableWithPageSize(pageSize: number) {
    return (
      <DataTable
        data={data}
        columns={columns}
        hideSearch
        manualPagination
        total={1}
        page={1}
        pageSize={pageSize}
        onPageChange={() => {}}
        onPageSizeChange={() => {}}
      />
    )
  }

  it('当前 pageSize 不在标准选项内时触发器显示该值，并出现在可选项中', async () => {
    const page = await render(tableWithPageSize(1))
    const trigger = page.getByRole('combobox')

    await expect.element(trigger).toHaveTextContent('1')
    await userEvent.click(trigger)
    await expect
      .element(page.getByRole('option', { name: '1', exact: true }))
      .toBeVisible()
    await expect
      .element(page.getByRole('option', { name: '10', exact: true }))
      .toBeVisible()
  })

  it('标准 pageSize 的触发器正常显示当前值', async () => {
    const page = await render(tableWithPageSize(20))
    await expect.element(page.getByRole('combobox')).toHaveTextContent('20')
  })
})
