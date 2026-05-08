import { describe, it, expect } from 'vitest'
import { detectPlatform, platformLabel } from './detectPlatform'

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

  it('platformLabel maps every id', () => {
    expect(platformLabel('youtube')).toBe('YouTube')
    expect(platformLabel('facebook')).toBe('Facebook')
    expect(platformLabel('tiktok')).toBe('TikTok')
    expect(platformLabel('instagram')).toBe('Instagram')
    expect(platformLabel('unsupported')).toBe('Unsupported')
  })
})
