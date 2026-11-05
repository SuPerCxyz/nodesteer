import { useState } from 'react'
import i18n from '@/i18n'
import { beforeAll, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { EditorPanel, useEditorPanel } from './editor-panel'

function Fixture() {
  const [value, setValue] = useState('')
  const panel = useEditorPanel(value)
  return (
    <>
      <button onClick={() => panel.change('first', () => setValue('first'))}>
        Edit first
      </button>
      <button onClick={() => panel.change('second', () => setValue('second'))}>
        Edit second
      </button>
      {panel.open && (
        <EditorPanel title='Draft' onClose={panel.close}>
          <input
            aria-label='Value'
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          <button onClick={() => panel.saved(value)}>Save</button>
        </EditorPanel>
      )}
      {panel.confirmation}
    </>
  )
}
beforeAll(async () => {
  await i18n.changeLanguage('en')
})
it('opens on demand, keeps dirty input when discard is canceled, and switches only after confirmation', async () => {
  const page = await render(<Fixture />)
  expect(page.getByRole('textbox').elements()).toHaveLength(0)
  await userEvent.click(page.getByRole('button', { name: 'Edit first' }))
  await userEvent.fill(page.getByRole('textbox'), 'unsaved')
  await userEvent.click(page.getByRole('button', { name: 'Edit second' }))
  const dialog = page.getByRole('alertdialog')
  await userEvent.click(
    dialog.getByRole('button', { name: 'Cancel', exact: true })
  )
  await expect.element(page.getByRole('textbox')).toHaveValue('unsaved')
  await userEvent.click(page.getByRole('button', { name: 'Edit second' }))
  await userEvent.click(
    dialog.getByRole('button', { name: 'Discard changes', exact: true })
  )
  await expect.element(page.getByRole('textbox')).toHaveValue('second')
  await userEvent.click(page.getByRole('button', { name: 'Save', exact: true }))
  await userEvent.click(
    page
      .getByRole('region')
      .getByRole('button', { name: 'Cancel', exact: true })
  )
  expect(page.getByRole('textbox').elements()).toHaveLength(0)
})
