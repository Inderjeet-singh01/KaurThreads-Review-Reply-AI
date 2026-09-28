import { Star } from 'lucide-react'
import { classNames } from '../lib/utils'

interface RatingStarsProps {
  rating: number | null
  size?: 'sm' | 'md' | 'lg'
  showValue?: boolean
}

const SIZES = {
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
  lg: 'h-5 w-5',
}

export function RatingStars({ rating, size = 'sm', showValue = false }: RatingStarsProps) {
  const value = rating ?? 0
  return (
    <div className="inline-flex items-center gap-1" aria-label={`Rating: ${value} out of 5`}>
      <div className="flex items-center gap-0.5">
        {[1, 2, 3, 4, 5].map((star) => (
          <Star
            key={star}
            className={classNames(
              SIZES[size],
              star <= value
                ? 'fill-amber-400 text-amber-400'
                : 'fill-slate-200 text-slate-200',
            )}
          />
        ))}
      </div>
      {showValue && rating != null && (
        <span className="ml-1 text-sm font-semibold text-slate-700">{rating.toFixed(1)}</span>
      )}
    </div>
  )
}
