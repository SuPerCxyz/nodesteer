import { type ImgHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Logo({
  className,
  alt = 'NodeSteer',
  ...props
}: ImgHTMLAttributes<HTMLImageElement>) {
  return (
    <img
      id='nodesteer-logo'
      src='/images/nodesteer-logo-96.webp'
      alt={alt}
      className={cn('size-6 object-contain', className)}
      {...props}
    />
  )
}
