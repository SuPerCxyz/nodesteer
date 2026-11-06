import '@tanstack/react-table'

declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    className?: string // apply to both th and td
    tdClassName?: string
    thClassName?: string
    /** Rack 行字段跨列。 */
    rackSpan?: number
    /** Rack 行字段语义宽度。 */
    rackWidth?: string
    /** Rack 字段允许两行；默认单行截断。 */
    rackMultiline?: boolean
    /** Rack 视图隐藏该列（如并入标题/副标题）。 */
    rackHidden?: boolean
  }
}
