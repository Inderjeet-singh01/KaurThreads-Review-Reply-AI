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
  const label = rating == null ? 'No rating' : `Rated ${rating} out of 5`
  return (
    <span className="inline-flex items-center gap-1" role="img" aria-label={label}>
      <span className="flex items-center gap-0.5">
        {[1, 2, 3, 4, 5].map((star) => {
          // Half star for averages such as 4.5.
          const fill = Math.max(0, Math.min(1, value - (star - 1)))
          return (
            <span key={star} className={classNames('relative', SIZES[size])}>
              <Star className={classNames('absolute inset-0 fill-slate-200 text-slate-200', SIZES[size])} />
              {fill > 0 && (
                <span className="absolute inset-0 overflow-hidden" style={{ width: `${fill * 100}%` }}>
                  <Star className={classNames('fill-amber-400 text-amber-400', SIZES[size])} />
                </span>
              )}
            </span>
          )
        })}
      </span>
      {showValue && rating != null && (
        <span className="ml-1 text-sm font-semibold text-slate-700">{rating.toFixed(1)}</span>
      )}
    </span>
  )
}
