import { describe, it, expect } from 'vitest'
import { scoreToGrade, formatTime, formatTimestamp } from './scoreToGrade'

describe('scoreToGrade', () => {
  it('returns — for null', () => expect(scoreToGrade(null)).toBe('—'))
  it('returns — for undefined', () => expect(scoreToGrade(undefined)).toBe('—'))
  it('returns A for 90-100', () => { expect(scoreToGrade(100)).toBe('A'); expect(scoreToGrade(90)).toBe('A') })
  it('returns A- for 80-89', () => { expect(scoreToGrade(85)).toBe('A-'); expect(scoreToGrade(80)).toBe('A-') })
  it('returns B+ for 70-79', () => { expect(scoreToGrade(75)).toBe('B+'); expect(scoreToGrade(70)).toBe('B+') })
  it('returns B for 60-69', () => { expect(scoreToGrade(65)).toBe('B'); expect(scoreToGrade(60)).toBe('B') })
  it('returns B- for 50-59', () => { expect(scoreToGrade(55)).toBe('B-'); expect(scoreToGrade(50)).toBe('B-') })
  it('returns C+ for <50', () => { expect(scoreToGrade(49)).toBe('C+'); expect(scoreToGrade(0)).toBe('C+') })
})

describe('formatTime', () => {
  it('formats 0 as 0:00', () => expect(formatTime(0)).toBe('0:00'))
  it('formats 65 as 1:05', () => expect(formatTime(65)).toBe('1:05'))
  it('formats 3600 as 60:00', () => expect(formatTime(3600)).toBe('60:00'))
})

describe('formatTimestamp', () => {
  it('formats 0 as 00:00', () => expect(formatTimestamp(0)).toBe('00:00'))
  it('formats 65 as 01:05', () => expect(formatTimestamp(65)).toBe('01:05'))
})
