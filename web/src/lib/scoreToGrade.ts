export function scoreToGrade(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 90) return 'A'
  if (n >= 80) return 'A-'
  if (n >= 70) return 'B+'
  if (n >= 60) return 'B'
  if (n >= 50) return 'B-'
  return 'C+'
}

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}
