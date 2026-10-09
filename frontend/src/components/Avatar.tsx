import { useState } from 'react'
import { avatarColor, classNames, initials } from '../lib/utils'

interface AvatarProps {
  name: string
  photoUrl?: string | null
  size?: 'xs' | 'sm' | 'md' | 'lg'
  /** Rounded square (businesses) instead of a circle (people). */
  square?: boolean
}

const SIZES = {
  xs: 'h-7 w-7 text-[11px]',
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-12 w-12 text-base',
}

export function Avatar({ name, photoUrl, size = 'md', square }: AvatarProps) {
  const [failed, setFailed] = useState(false)
  const showImage = photoUrl && !failed

  return (
    <div
      className={classNames(
        'flex shrink-0 items-center justify-center overflow-hidden font-semibold text-white',
        square ? 'rounded-lg' : 'rounded-full',
        SIZES[size],
        showImage ? 'bg-slate-200' : avatarColor(name),
      )}
      aria-hidden
    >
      {showImage ? (
        <img
          src={photoUrl}
          alt=""
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
          referrerPolicy="no-referrer"
          loading="lazy"
        />
      ) : (
        initials(name)
      )}
    </div>
  )
}
