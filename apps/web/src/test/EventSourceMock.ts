type Listener = (event: MessageEvent<string>) => void

export class EventSourceMock {
  static instances: EventSourceMock[] = []

  readonly url: string
  readonly listeners = new Map<string, Listener[]>()
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    EventSourceMock.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener as Listener)
    this.listeners.set(type, listeners)
  }

  close() {}

  emit(type: string, payload: Record<string, unknown>) {
    const event = new MessageEvent(type, {
      data: JSON.stringify(payload),
    })
    ;(this.listeners.get(type) ?? []).forEach((listener) => listener(event))
  }

  static reset() {
    EventSourceMock.instances = []
  }
}

export function installEventSourceMock() {
  EventSourceMock.reset()
  globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
}
