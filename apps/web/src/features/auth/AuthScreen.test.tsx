import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AuthScreen } from './AuthScreen'

describe('AuthScreen', () => {
  const tr = (zh: string, en: string) => en || zh

  it('renders the sign-in surface', () => {
    render(
      <AuthScreen
        error=""
        initializing={false}
        mode="login"
        notice=""
        onChangeMode={vi.fn()}
        onLogin={vi.fn()}
        onRegister={vi.fn()}
        tr={tr}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Sign In To Continue' })).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
  })

  it('submits the login form with typed credentials', () => {
    const onLogin = vi.fn()

    render(
      <AuthScreen
        error=""
        initializing={false}
        mode="login"
        notice=""
        onChangeMode={vi.fn()}
        onLogin={onLogin}
        onRegister={vi.fn()}
        tr={tr}
      />,
    )

    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'founder' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret123' } })
    fireEvent.submit(screen.getByRole('button', { name: 'Enter Console' }).closest('form')!)

    expect(onLogin).toHaveBeenCalledWith({
      username: 'founder',
      password: 'secret123',
    })
  })
})
