import { describe, expect, it } from 'vitest'
import { buildMemoryListPath, buildRecallMemoryPath } from './api'

describe('memory api path builders', () => {
  it('keeps optional list params out when they are missing', () => {
    expect(buildMemoryListPath()).toBe('/v1/memories')
  })

  it('encodes recall params and optional admin user id', () => {
    expect(buildRecallMemoryPath('release notes', 5, 'alice@example.com')).toBe(
      '/v1/memories/recall?query=release+notes&limit=5&user_id=alice%40example.com',
    )
  })
})
