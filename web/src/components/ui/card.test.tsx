import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { Card, CardContent, CardHeader, CardTitle } from './card'

describe('Card spacing', () => {
  it('uses compact base spacing and unified header padding', async () => {
    const page = await render(
      <Card data-testid='card'>
        <CardHeader data-testid='header'>
          <CardTitle>Title</CardTitle>
        </CardHeader>
        <CardContent data-testid='content'>Content</CardContent>
      </Card>
    )

    const card = page.getByTestId('card').element()
    const header = page.getByTestId('header').element()
    const content = page.getByTestId('content').element()

    expect(card.className).toContain('gap-3')
    expect(card.className).toContain('py-4')
    expect(card.className).not.toContain('gap-6')
    expect(card.className).not.toContain('py-6')
    expect(header.className).toContain('px-6')
    expect(header.className).toContain('pb-1')
    expect(header.className).not.toContain('pb-3')
    expect(header.className).not.toContain('[.border-b]:pb-6')
    expect(header.className).not.toContain('grid-rows-[auto_auto]')
    expect(content.className).toContain('px-6')

    await page.unmount()
  })
})
