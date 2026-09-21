import './Toast.css'

export type ToastVariant = 'info' | 'success' | 'danger'

export interface ToastProps {
  message: string
  variant?: ToastVariant
}

const ICON: Record<ToastVariant, string> = {
  info: 'i',
  success: '✓',
  danger: '!',
}

export function Toast({ message, variant = 'info' }: ToastProps) {
  return (
    <div className={`toast toast--${variant}`} role="status" aria-live="polite">
      <span className="toast__icon" aria-hidden="true">
        {ICON[variant]}
      </span>
      <span className="toast__message">{message}</span>
    </div>
  )
}
