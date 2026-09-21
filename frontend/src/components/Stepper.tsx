import './Stepper.css'

export interface StepperProps {
  label: string
  value: number
  min?: number
  max?: number
  step?: number
  onChange?: (value: number) => void
  disabled?: boolean
}

export function Stepper({
  label,
  value,
  min = 0,
  max = 99,
  step = 1,
  onChange,
  disabled = false,
}: StepperProps) {
  const atMin = value <= min
  const atMax = value >= max

  return (
    <div className="stepper">
      <span className="stepper__label" id={`stepper-${label}`}>
        {label}
      </span>
      <div className="stepper__controls">
        <button
          type="button"
          className="stepper__btn"
          aria-label={`Decrease ${label}`}
          disabled={disabled || atMin}
          onClick={() => onChange?.(Math.max(min, value - step))}
        >
          −
        </button>
        <output className="stepper__value tnum" aria-labelledby={`stepper-${label}`}>
          {value}
        </output>
        <button
          type="button"
          className="stepper__btn"
          aria-label={`Increase ${label}`}
          disabled={disabled || atMax}
          onClick={() => onChange?.(Math.min(max, value + step))}
        >
          +
        </button>
      </div>
    </div>
  )
}
