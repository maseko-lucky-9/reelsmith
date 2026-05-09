import { describe, it, expect } from 'vitest'
import { detectPlatform, isShortFormUrl, platformLabel } from './detectPlatform'

describe('detectPlatform', () => {
  it.each([
    ['https://www.youtube.com/watch?v=abc', 'youtube'],
    ['https://youtu.be/abc', 'youtube'],
    ['https://m.youtube.com/watch?v=abc', 'youtube'],
  ])('detects YouTube: %s', (url, expected) => {
    expect(detectPlatform(url)).toBe(expected)
  })

  it.each([
    ['https://www.facebook.com/reel/123', 'facebook'],
    ['https://m.facebook.com/watch/?v=1', 'facebook'],
    ['https://fb.watch/abc/', 'facebook'],
  ])('detects Facebook: %s', (url, expected) => {
    expect(detectPlatform(url)).toBe(expected)
  })

  it.each([
    ['https://www.tiktok.com/@u/video/1', 'tiktok'],
    ['https://vm.tiktok.com/abc/', 'tiktok'],
  ])('detects TikTok: %s', (url, expected) => {
    expect(detectPlatform(url)).toBe(expected)
  })

  it.each([
    ['https://www.instagram.com/reel/abc/', 'instagram'],
    ['https://instagr.am/p/abc/', 'instagram'],
  ])('detects Instagram: %s', (url, expected) => {
    expect(detectPlatform(url)).toBe(expected)
  })

  it('detects upload scheme', () => {
    expect(detectPlatform('upload:///tmp/x.mp4')).toBe('upload')
  })

  it.each([
    'https://eviltiktok.com/foo',
    'https://youtube.com.attacker.io/watch',
    'https://example.com/v',
    '',
    'not-a-url',
  ])('rejects unsupported / confusable: %s', (url) => {
    expect(detectPlatform(url)).toBe('unsupported')
  })

  it.each([
    ['https://www.youtube.com/shorts/abc', true],
    ['https://youtube.com/shorts/xyz?si=foo', true],
    ['https://m.youtube.com/shorts/abc', true],
    ['https://www.tiktok.com/@u/video/1', true],
    ['https://vt.tiktok.com/ABC/', true],
    ['https://www.instagram.com/reel/abc/', true],
    ['https://instagr.am/p/abc/', true],
    ['https://www.youtube.com/watch?v=abc', false],
    ['https://youtu.be/abc', false],
    ['https://www.facebook.com/share/r/123/', false],
    ['', false],
    ['not-a-url', false],
  ])('isShortFormUrl(%s) === %s', (url, expected) => {
    expect(isShortFormUrl(url)).toBe(expected)
  })

  it('platformLabel maps every id', () => {
    expect(platformLabel('youtube')).toBe('YouTube')
    expect(platformLabel('facebook')).toBe('Facebook')
    expect(platformLabel('tiktok')).toBe('TikTok')
    expect(platformLabel('instagram')).toBe('Instagram')
    expect(platformLabel('unsupported')).toBe('Unsupported')
  })
})
