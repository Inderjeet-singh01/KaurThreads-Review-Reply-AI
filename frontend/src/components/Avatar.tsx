import { useState } from 'react'
import { avatarColor, classNames, initials } from '../lib/utils'

interface AvatarProps {
  name: string
  photoUrl?: string | null
  size?: 'sm' | 'md' | 'lg'
}

const SIZES = {
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-12 w-12 text-base',
}

export function Avatar({ name, photoUrl, size = 'md' }: AvatarProps) {
  const [failed, setFailed] = useState(false)
  const showImage = photoUrl && !failed

  return (
    <div
      className={classNames(
        'flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold text-white',
        SIZES[size],
        showImage ? 'bg-slate-200' : avatarColor(name),
      )}
    >
      {showImage ? (
        <img
          src={photoUrl}
          alt={name}
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
          referrerPolicy="no-referrer"
        />
      ) : (
        initials(name)
      )}
    </div>
  )
}
