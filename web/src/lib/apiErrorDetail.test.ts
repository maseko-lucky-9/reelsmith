import { describe, it, expect } from 'vitest'
import { apiErrorDetail } from './apiErrorDetail'

describe('apiErrorDetail', () => {
  it('returns the detail of a status-prefixed JSON error', () => {
    const err = new Error('409 {"detail":"source video not retained"}')
    expect(apiErrorDetail(err)).toBe('source video not retained')
  })

  it.each([
    ['a plain-text body', new Error('500 Internal Server Error')],
    ['a JSON body without detail', new Error('400 {"message":"nope"}')],
    ['a validation error list', new Error('422 {"detail":[{"msg":"bad"}]}')],
    ['a non-Error value', 'boom'],
  ])('returns null for %s', (_label, err) => {
    expect(apiErrorDetail(err)).toBeNull()
  })
})
