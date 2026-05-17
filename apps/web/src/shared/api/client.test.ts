import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest } from './client'

describe('apiRequest', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('attaches credentials and parses json responses', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        headers: { 'Content-Type': 'application/json' },
        status: 200,
      }),
    )

    await expect(apiRequest<{ ok: boolean }>('/v1/ping')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/ping',
      expect.objectContaining({
        credentials: 'include',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    )
  })

  it('throws ApiError with payload detail for non-2xx responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'forbidden' }), {
        headers: { 'Content-Type': 'application/json' },
        status: 403,
        statusText: 'Forbidden',
      }),
    )

    await expect(apiRequest('/v1/admin')).rejects.toEqual(
      new ApiError('forbidden', 403, { error: 'forbidden' }),
    )
  })
})
