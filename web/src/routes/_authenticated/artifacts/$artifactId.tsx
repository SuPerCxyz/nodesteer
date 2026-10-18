import { createFileRoute } from '@tanstack/react-router'
import { ArtifactDetail } from '@/features/catalog'

export const Route = createFileRoute('/_authenticated/artifacts/$artifactId')({
  component: ArtifactDetail,
})
