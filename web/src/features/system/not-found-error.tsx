import { Link } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'

export function NotFoundError() {
  return (
    <div className='container flex min-h-svh flex-col items-center justify-center gap-4 text-center'>
      <h1 className='text-3xl font-semibold'>Page not found</h1>
      <p className='text-muted-foreground'>
        The requested NodeSteer page does not exist.
      </p>
      <Button asChild>
        <Link to='/'>Back to overview</Link>
      </Button>
    </div>
  )
}
